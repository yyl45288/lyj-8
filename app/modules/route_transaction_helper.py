from typing import Callable, Any, Type, Tuple
from functools import wraps
from sqlalchemy.orm import Session
from fastapi import HTTPException


class TransactionalRouteHelper:
    @staticmethod
    def handle(
        operation: Callable[[Session], Any],
        db: Session,
        bad_request_errors: Tuple[Type[Exception], ...] = (),
        error_400_msg: str = None,
        error_500_msg: str = "操作失败",
        success_callback: Callable[[Any], Any] = None
    ):
        try:
            result = operation(db)
            db.commit()
            if success_callback:
                return success_callback(result)
            return result
        except bad_request_errors as e:
            db.rollback()
            raise HTTPException(400, error_400_msg or str(e))
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, f"{error_500_msg}: {str(e)}")


def transactional_route(
    bad_request_errors: Tuple[Type[Exception], ...] = (),
    error_500_msg: str = "操作失败"
):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            db = kwargs.get("db")
            for arg in args:
                if isinstance(arg, Session):
                    db = arg
                    break

            try:
                result = func(*args, **kwargs)
                if db:
                    db.commit()
                return result
            except bad_request_errors as e:
                if db:
                    db.rollback()
                raise HTTPException(400, str(e))
            except HTTPException:
                if db:
                    db.rollback()
                raise
            except Exception as e:
                if db:
                    db.rollback()
                raise HTTPException(500, f"{error_500_msg}: {str(e)}")
        return wrapper
    return decorator
