from typing import Callable, Any, Optional, Type, Tuple
from contextlib import contextmanager
from sqlalchemy.orm import Session
from app.database import get_db
from app.modules.unit_of_work import UnitOfWork, TransactionError


class ApplicationService:
    def __init__(self, db: Session):
        self.db = db
        self.uow = UnitOfWork(db)

    def execute(self, operation: Callable[[Session], Any],
                error_types: Optional[Tuple[Type[Exception], ...]] = None,
                error_message: str = "操作失败") -> Any:
        try:
            result = operation(self.db)
            self.uow.commit()
            return result
        except Exception as e:
            self.uow.rollback()
            if error_types and isinstance(e, error_types):
                raise
            raise TransactionError(f"{error_message}: {str(e)}") from e


@contextmanager
def transaction_scope(db: Session):
    uow = UnitOfWork(db)
    try:
        yield uow
        uow.commit()
    except Exception:
        uow.rollback()
        raise


def run_with_transaction(db: Session,
                         func: Callable[..., Any],
                         *args, **kwargs) -> Any:
    uow = UnitOfWork(db)
    try:
        result = func(*args, **kwargs)
        uow.commit()
        return result
    except Exception:
        uow.rollback()
        raise
