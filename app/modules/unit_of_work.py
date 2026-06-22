from typing import Callable, Any, Optional, Type, Tuple
from contextlib import contextmanager
from sqlalchemy.orm import Session
from app.database import SessionLocal


class TransactionError(Exception):
    pass


class UnitOfWork:
    def __init__(self, db: Optional[Session] = None):
        self.db = db or SessionLocal()
        self._external_db = db is not None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._external_db:
            try:
                if exc_type is not None:
                    self.rollback()
                else:
                    self.commit()
            finally:
                self.close()
        return False

    def commit(self) -> None:
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise TransactionError(f"事务提交失败: {str(e)}") from e

    def rollback(self) -> None:
        self.db.rollback()

    def close(self) -> None:
        if not self._external_db:
            self.db.close()

    def flush(self) -> None:
        self.db.flush()

    @contextmanager
    def nested(self):
        try:
            with self.db.begin_nested():
                yield self
        except Exception as e:
            raise TransactionError(f"嵌套事务失败: {str(e)}") from e


def with_transaction(
    error_types: Tuple[Type[Exception], ...] = (Exception,),
    error_message: str = "操作失败"
):
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            uow = None
            for arg in args:
                if isinstance(arg, UnitOfWork):
                    uow = arg
                    break
            if "uow" in kwargs and isinstance(kwargs["uow"], UnitOfWork):
                uow = kwargs["uow"]

            if uow is None:
                uow = UnitOfWork()
                kwargs["uow"] = uow

            try:
                result = func(*args, **kwargs)
                if not uow._external_db:
                    uow.commit()
                return result
            except error_types as e:
                if not uow._external_db:
                    uow.rollback()
                raise
            except Exception as e:
                if not uow._external_db:
                    uow.rollback()
                raise TransactionError(f"{error_message}: {str(e)}") from e
            finally:
                if uow and not uow._external_db:
                    uow.close()
        return wrapper
    return decorator
