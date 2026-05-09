# app/logger.py
import threading
import queue
from datetime import datetime
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.sys_log import SysLog

class AsyncDBLogger:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_queue()
        return cls._instance

    def _init_queue(self):
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self._consume, daemon=True)
        self.thread.start()

    def _consume(self):
        while True:
            log_entry = self.queue.get()
            if log_entry is None:
                break
            self._write_to_db(**log_entry)
            self.queue.task_done()

    def _write_to_db(self, **kwargs):
        db: Session = SessionLocal()
        try:
            sys_log = SysLog(
                created_at=datetime.utcnow(),
                **kwargs
            )
            db.add(sys_log)
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Log insert failed: {e}")
        finally:
            db.close()

    def log(self, **kwargs):
        self.queue.put(kwargs)

# 这里创建一个全局实例
logger = AsyncDBLogger()