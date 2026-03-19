```python
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel
from ..app import get_current_active_user, User

router = APIRouter()

class LogEntry(BaseModel):
    timestamp: datetime
    level: str
    message: str
    source: str
    details: Optional[dict] = None

class LogFilter(BaseModel):
    level: Optional[str] = None
    source: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    search: Optional[str] = None

# Mock database
mock_logs_db = [
    {
        "timestamp": datetime.utcnow(),
        "level": "ERROR",
        "message": "Connection timeout",
        "source": "network-service",
        "details": {"attempts": 3, "last_error": "ETIMEDOUT"}
    },
    {
        "timestamp": datetime.utcnow(),
        "level": "INFO",
        "message": "Service started",
        "source": "auth-service",
        "details": {"version": "1.2.3"}
    }
]

@router.get("/", response_model=List[LogEntry])
async def read_logs(
    filter: LogFilter = Depends(),
    current_user: User = Depends(get_current_active_user)
):
    try:
        filtered_logs = mock_logs_db

        if filter.level:
            filtered_logs = [log for log in filtered_logs if log["level"] == filter.level]
        if filter.source:
            filtered_logs = [log for log in filtered_logs if log["source"] == filter.source]
        if filter.start_time:
            filtered_logs = [log for log in filtered_logs if log["timestamp"] >= filter.start_time]
        if filter.end_time:
            filtered_logs = [log for log in filtered_logs if log["timestamp"] <= filter.end_time]
        if filter.search:
            filtered_logs = [
                log for log in filtered_logs
                if (filter.search.lower() in log["message"].lower() or
                    filter.search.lower() in log["source"].lower())
            ]

        return filtered_logs
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error filtering logs: {str(e)}"
        )

@router.get("/{log_id}", response_model=LogEntry)
async def read_log(
    log_id: int,
    current_user: User = Depends(get_current_active_user)
):
    try:
        if log_id < 0 or log_id >= len(mock_logs_db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Log entry not found"
            )
        return mock_logs_db[log_id]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving log: {str(e)}"
        )
```