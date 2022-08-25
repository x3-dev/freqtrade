"""
This module contains the class to persist trades into SQLite
"""
import logging

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import NoSuchModuleError
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import StaticPool

from freqtrade.exceptions import OperationalException
from freqtrade.persistence.base import _DECL_BASE
from freqtrade.persistence.migrations import check_migrate
from freqtrade.persistence.pairlock import PairLock
from freqtrade.persistence.trade_model import Order, Trade

import re
from sqlalchemy_utils import database_exists, create_database
from sqlalchemy import desc, func, event, text, DDL
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.schema import CreateSchema
from sqlalchemy.sql import exists, select

logger = logging.getLogger(__name__)


_SQL_DOCS_URL = 'http://docs.sqlalchemy.org/en/latest/core/engines.html#database-urls'


def init_db(config: Dict) -> None:
    """
    Initializes this module with the given config,
    registers all known command handlers
    and starts polling for message updates
    :param db_url: Database to use
    :return: None
    """

    db_url: str = config.get('db_url', None)
    schema: str = config.get('db_schema', None)
    clean_open_orders: bool = config.get('dry_run', False)

    kwargs, __schema__ = {}, None

    if db_url == 'sqlite:///':
        raise OperationalException(
            f'Bad db-url {db_url}. For in-memory database, please use `sqlite://`.')
    if db_url == 'sqlite://':
        kwargs.update({
            'poolclass': StaticPool,
        })
    # Take care of thread ownership
    if db_url.startswith('sqlite://'):
        kwargs.update({
            'connect_args': {'check_same_thread': False},
        })

    """
        Hack to manage multiple bots in a single database using the advantage of PostgreSql schemata
        Docs: https://www.postgresql.org/docs/13/ddl-schemas.html
        Install: psycopg2 sqlalchemy-utils
        Caveats:
            - if running bot through unix socket by setting db_url='postgresql+psycopg2:///dbname' the user running bot script eg. freqtrade must exist as ROLE in PostgreSql
            - if database is not exists and should be created by bot PostgreSql ROLE must have create privileges
        Summary:
            - each bot is using its own namespace/schema with its tables
            - schema is created on db_init method while bot is starting
            - schema name is adopted through configuration variable 'bot_name'
    """
    if db_url.startswith('postgresql'):
        __schema__ = re.sub('[^0-9a-zA-Z\-]', '_', schema or 'public').lower()
        _DECL_BASE.metadata.schema = __schema__
        kwargs.update({
            'connect_args': {'options': f'-csearch_path={__schema__}'},
        })

    try:
        engine = create_engine(db_url, future=True, **kwargs)
    except NoSuchModuleError:
        raise OperationalException(f"Given value for db_url: '{db_url}' "
                                   f"is no valid database URL! (See {_SQL_DOCS_URL})")

    if not database_exists(engine.url):
        logger.info(f"database '{engine.url.database}' does not exists, creating")
        try:
            create_database(engine.url)
        except Exception as err:
            raise OperationalException(f"Error occured while creating database: {err}")

    if db_url.startswith('postgresql'):
        if not __schema__ or __schema__ is None:
            raise OperationalException(
                f"Error occured: 'schema name is not provided, probably configuration file has no ´bot_name´ entry!'"
            )
        if __schema__.startswith('pg_'):
            raise OperationalException(f"Error occured: schema name should not start with 'pg_'")

        if not __schema__ in inspect(engine).get_schema_names():
            logger.info(f"Schema '{__schema__}' does not exists, creating...")
            try:
                event.listen(_DECL_BASE.metadata, 'before_create', CreateSchema(__schema__))
            except ProgrammingError as err:
                raise OperationalException(f"Error occured: '{err}'")

    # https://docs.sqlalchemy.org/en/13/orm/contextual.html#thread-local-scope
    # Scoped sessions proxy requests to the appropriate thread-local session.
    # We should use the scoped_session object - not a seperately initialized version
    Trade._session = scoped_session(sessionmaker(bind=engine, autoflush=False))
    Trade.query = Trade._session.query_property()
    Order.query = Trade._session.query_property()
    PairLock.query = Trade._session.query_property()

    previous_tables = inspect(engine).get_table_names()
    _DECL_BASE.metadata.create_all(engine)
    check_migrate(engine, decl_base=_DECL_BASE, previous_tables=previous_tables)
