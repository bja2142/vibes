from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.sql import func
from .database import Base

class Puzzle(Base):
    __tablename__ = "puzzles"

    id = Column(Integer, primary_key=True, index=True)
    hash_id = Column(String, unique=True, index=True)
    theme = Column(String, index=True)
    age_range = Column(String)
    style = Column(String, index=True)
    color_mode = Column(String)
    ink_saver = Column(Boolean, default=False)
    words = Column(Text, index=True) # Comma-separated for searching
    status = Column(String, default="pending") # pending, completed, failed, awaiting_review
    status_message = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    review_required = Column(Boolean, default=False)
    styling_attempts = Column(Integer, default=0)
    has_styled = Column(Boolean, default=False)
    suggested_prompt = Column(Text, nullable=True)
    model_name = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
