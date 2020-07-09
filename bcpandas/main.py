# -*- coding: utf-8 -*-
"""
Created on Sat Aug  3 23:07:15 2019

@author: ydima
"""

import csv
import logging
import os
from typing import Dict, Optional, Union
from urllib.parse import quote_plus

import pandas as pd
from pandas.io.sql import SQLDatabase, SQLTable
import sqlalchemy as sa

from .constants import (
    IF_EXISTS_OPTIONS,
    IN,
    NEWLINE,
    TABLE,
    BCPandasValueError,
    get_delimiter,
    get_quotechar,
)
from .utils import bcp, build_format_file, get_temp_file

logger = logging.getLogger(__name__)


class SqlCreds:
    """
    Credential object for all SQL operations. Will automatically also create a SQLAlchemy 
    engine that uses `pyodbc` as the DBAPI, and store it in the `self.engine` attribute.

    If `username` and `password` are not provided, `with_krb_auth` will be `True`.

    Only supports SQL based logins, not Active Directory or Azure AD.

    Parameters
    ----------
    server : str
    database : str
    username : str, optional
    password : str, optional
    driver_version : int, default 17
        The version of the Microsoft ODBC Driver for SQL Server to use 
    odbc_kwargs : dict of {str, str or int}, optional
        additional keyword arguments, to pass into ODBC connection string, 
        such as Encrypted='yes'
    
    Returns
    -------
    `bcpandas.SqlCreds`
    """

    def __init__(
        self,
        server: str,
        database: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        driver_version: int = 17,
        port: int = 1433,
        odbc_kwargs: Optional[Dict[str, Union[str, int]]] = None,
    ):
        self.server = server
        self.database = database
        self.port = port

        self.driver = f"{{ODBC Driver {driver_version} for SQL Server}}"

        # Append a comma for use in connection strings (optionally blank)
        if port:
            port_str = f",{self.port}"
        else:
            port_str = ""

        db_url = (
            f"Driver={self.driver};Server=tcp:{self.server}{port_str};Database={self.database};"
        )
        if username and password:
            self.username = username
            self.password = password
            self.with_krb_auth = False
            db_url += f"UID={username};PWD={password}"
        else:
            self.username = ""
            self.password = ""
            self.with_krb_auth = True
            db_url += "Trusted_Connection=yes;"

        logger.info(f"Created creds:\t{self}")

        # construct the engine for sqlalchemy
        if odbc_kwargs:
            db_url += ";".join(f"{k}={v}" for k, v in odbc_kwargs.items())
        conn_string = f"mssql+pyodbc:///?odbc_connect={quote_plus(db_url)}"
        self.engine = sa.engine.create_engine(conn_string)

        # don't print password to logs!
        # logger.info(f"Created engine for sqlalchemy:\t{self.engine}")

    @classmethod
    def from_engine(cls, engine: sa.engine.base.Engine) -> "SqlCreds":
        """
        Alternate constructor, from a `sqlalchemy.engine.base.Engine` that uses `pyodbc` as the DBAPI 
        (which is the SQLAlchemy default for MS SQL) and using an exact PyODBC connection string (not DSN or hostname).
        See https://docs.sqlalchemy.org/en/13/dialects/mssql.html#connecting-to-pyodbc for more.
        
        Parameters
        ----------
        engine : `sqlalchemy.engine.base.Engine`
            The SQLAlchemy engine object, configured as described above

        Returns
        -------
        `bcpandas.SqlCreds`
        """
        try:
            # get the odbc url part from the engine, split by ';' delimiter
            conn_url = engine.url.query["odbc_connect"].split(";")
            # convert into dict
            conn_dict = {x.split("=")[0]: x.split("=")[1] for x in conn_url if "=" in x}

            if "," in conn_dict["Server"]:
                conn_dict["port"] = int(conn_dict["Server"].split(",")[1])

            sql_creds = cls(
                server=conn_dict["Server"].replace("tcp:", "").split(",")[0],
                database=conn_dict["Database"],
                username=conn_dict.get("UID", None),
                password=conn_dict.get("PWD", None),
                port=conn_dict.get("port", None),
            )

            # add Engine object as attribute
            sql_creds.engine = engine
            return sql_creds
        except (KeyError, AttributeError) as ex:
            raise BCPandasValueError(
                f"The supplied 'engine' object could not be parsed correctly, try creating a SqlCreds object manually."
                f"\nOriginal Error: \n {ex}"
            )

    def __repr__(self):
        # adopted from https://github.com/erdewit/ib_insync/blob/master/ib_insync/objects.py#L51
        clsName = self.__class__.__qualname__
        kwargs = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items() if k != "password")
        if hasattr(self, "password"):
            kwargs += ", password=[REDACTED]"
        return f"{clsName}({kwargs})"

    __str__ = __repr__


def _sql_item_exists(sql_type: str, schema: str, table_name: str, creds: SqlCreds) -> bool:
    _qry = """
        SELECT * 
        FROM INFORMATION_SCHEMA.{_typ}S 
        WHERE TABLE_SCHEMA = '{_schema}' 
        AND TABLE_NAME = '{_tbl}'
        """.format(
        _typ=sql_type.upper(), _schema=schema, _tbl=table_name
    )
    res = pd.read_sql_query(sql=_qry, con=creds.engine)
    return res.shape[0] > 0


def _create_table(schema: str, table_name: str, creds: SqlCreds, df: pd.DataFrame, if_exists: str,
                  dtype: dict, keys: list):
    """use pandas' own code to create the table and schema"""

    sql_db = SQLDatabase(engine=creds.engine, schema=schema)
    table = SQLTable(
        table_name,
        sql_db,
        frame=df,
        index=False,  # already set as new col earlier if index=True
        if_exists=if_exists,
        index_label=None,
        schema=schema,
        dtype=dtype,
        keys=keys
    )
    table.create()


def to_sql(
    df: pd.DataFrame,
    table_name: str,
    creds: SqlCreds,
    sql_type: str = "table",
    schema: str = "dbo",
    index: bool = True,
    if_exists: str = "fail",
    batch_size: int = None,
    debug: bool = False,
    bcp_path: str = None,
    dtypes: dict = None,
    keys: list = None,
    error_path: str = None
):
    """
    Writes the pandas DataFrame to a SQL table or view.

    Will write all columns to the table or view. If the destination table/view doesn't exist, will create it.
    Assumes the SQL table/view has the same number, name, and type of columns.
    To only write parts of the DataFrame, filter it beforehand and pass that to this function.
    Unlike the pandas counterpart, if the DataFrame has no rows, nothing will happen.

    Parameters
    ----------
    df : pandas.DataFrame
    table_name : str
        Name of SQL table or view, without the schema
    creds : bcpandas.SqlCreds
        The credentials used in the SQL database.
    sql_type : {'table'}, can only be 'table'
        The type of SQL object of the destination.
    schema : str, default 'dbo'
        The SQL schema.
    index : bool, default True
        Write DataFrame index as a column. Uses the index name as the column
        name in the table.
    if_exists : {'fail', 'replace', 'append'}, default 'fail'
        How to behave if the table already exists.
        * fail: Raise a BCPandasValueError.
        * replace: Drop the table before inserting new values.
        * append: Insert new values to the existing table. Matches the dataframe columns to the database columns by name.
            If the database table exists then the dataframe cannot have new columns that aren't in the table, 
            but conversely table columns can be missing from the dataframe.
    batch_size : int, optional
        Rows will be written in batches of this size at a time. By default, BCP sets this to 1000.
    debug : bool, default False
        If True, will not delete the temporary CSV and format files, and will output their location.
    bcp_path : str, default None
        The full path to the BCP utility, useful if it is not in the PATH environment variable
    dtypes: dict, default None
        Dictionary with key as column name value as sqlalchemy data type
    keys: list, default None
        List of columns that define the primary key
    error_path: str, default None
        Path for bcp error file
    """
    # validation
    if df.shape[0] == 0 or df.shape[1] == 0:
        return
    assert sql_type == TABLE, "only supporting table, not view, for now"
    assert if_exists in IF_EXISTS_OPTIONS

    if df.columns.has_duplicates:
        raise BCPandasValueError(
            "Columns with duplicate names detected, SQL requires that column names be unique. "
            f"Duplicates: {df.columns[df.columns.duplicated(keep=False)]}"
        )

    # TODO diff way to implement? could be big performance hit with big dataframe
    if index:
        df = df.copy(deep=True).reset_index()

    delim = get_delimiter(df)
    quotechar = get_quotechar(df)

    if batch_size is not None:
        if batch_size == 0:
            raise BCPandasValueError("Param batch_size can't be 0")
        if batch_size > df.shape[0]:
            raise BCPandasValueError(
                "Param batch_size can't be larger than the number of rows in the DataFrame"
            )

    # save to temp path
    csv_file_path = get_temp_file()
    # replace bools with 1 or 0, this is what pandas native does when writing to SQL Server
    df.replace({True: 1, False: 0}).to_csv(
        path_or_buf=csv_file_path,
        sep=delim,
        header=False,
        index=False,  # already set as new col earlier if index=True
        quoting=csv.QUOTE_MINIMAL,  # pandas default
        quotechar='"',  # quotechar
        line_terminator=NEWLINE,
        # escapechar=None,  # not needed, as using doublequote
        # escapechar='\\'
    )
    logger.debug(f"Saved dataframe to temp CSV file at {csv_file_path}")

    # build format file
    fmt_file_path = get_temp_file()

    sql_item_exists = _sql_item_exists(
        sql_type=sql_type, schema=schema, table_name=table_name, creds=creds
    )
    cols_dict = None  # for mypy
    if if_exists == "append":
        # get dict of column names -> order of column
        cols_dict = dict(
            pd.read_sql_query(
                """
                SELECT COLUMN_NAME, ORDINAL_POSITION 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = '{_schema}'
                AND TABLE_NAME = '{_tbl}'
            """.format(
                    _schema=schema, _tbl=table_name
                ),
                creds.engine,
            ).values
        )

        # check that column names match in db and dataframe exactly
        if sql_item_exists:
            # the db cols are always strings, unlike df cols
            extra_cols = [str(x) for x in df.columns if str(x) not in cols_dict.keys()]
            if extra_cols:
                raise BCPandasValueError(
                    f"Column(s) detected in the dataframe that are not in the database, "
                    f"cannot have new columns if `if_exists=='append'`, "
                    f"the extra column(s): {extra_cols}"
                )

    fmt_file_txt = build_format_file(df=df, delimiter=delim, db_cols_order=cols_dict)
    with open(fmt_file_path, "w") as ff:
        ff.write(fmt_file_txt)
    logger.debug(f"Created BCP format file at {fmt_file_path}")

    try:
        if if_exists == "fail":
            if sql_item_exists:
                raise BCPandasValueError(
                    f"The {sql_type} called {schema}.{table_name} already exists, "
                    f"`if_exists` param was set to `fail`."
                )
            else:
                _create_table(
                    schema=schema, table_name=table_name, creds=creds, df=df, if_exists=if_exists,
                    dtype=dtypes, keys=keys
                )
        elif if_exists == "replace":
            _create_table(
                schema=schema, table_name=table_name, creds=creds, df=df, if_exists=if_exists,
                dtype=dtypes, keys=keys
            )
        elif if_exists == "append":
            if not sql_item_exists:
                _create_table(
                    schema=schema, table_name=table_name, creds=creds, df=df, if_exists=if_exists,
                    dtype=dtypes, keys=keys
                )

        # BCP the data in
        bcp(
            sql_item=table_name,
            direction=IN,
            flat_file=csv_file_path,
            format_file_path=fmt_file_path,
            creds=creds,
            sql_type=sql_type,
            schema=schema,
            batch_size=batch_size,
            bcp_path=bcp_path,
            error_file_path=f'{error_path}/{table_name}_bcp_error.txt'
        )
    finally:
        if not debug:
            logger.debug(f"Deleting temp CSV and format files")
            os.remove(csv_file_path)
            os.remove(fmt_file_path)
        else:
            logger.debug(
                f"`to_sql` DEBUG mode, not deleting the files. CSV file is at "
                f"{csv_file_path}, format file is at {fmt_file_path}"
            )
