from sqlalchemy import Column, Integer, String, Float, JSON
from database import Base
import time

class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    service = Column(String, index=True)
    confidence = Column(Float)
    votes = Column(JSON) # Store list of 5 ints as JSON
    action = Column(String)
    pod_name = Column(String)
    status = Column(String) # HEALED | FAILED | PENDING
    timestamp = Column(Float, default=time.time)
