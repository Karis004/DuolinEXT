from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint


class Word(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    text: str = Field(index=True, unique=True)
    translations_json: str = "[]"
    audio_url: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    study_count: int = 0
    review_count: int = 0
    last_studied_at: datetime | None = None
    last_reviewed_at: datetime | None = None


class Lexeme(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("lemma", "part_of_speech"),)

    id: int | None = Field(default=None, primary_key=True)
    lemma: str = Field(index=True)
    part_of_speech: str = "其他"
    gender: str | None = None
    meaning: str = ""
    usage_note: str = ""
    generation_status: str = "pending"
    generation_version: int = 0
    generated_at: datetime | None = None
    updated_at: datetime | None = None


class LexemeForm(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("lexeme_id", "form", "label"),)

    id: int | None = Field(default=None, primary_key=True)
    lexeme_id: int = Field(foreign_key="lexeme.id", index=True)
    form: str
    label: str
    note: str = ""
    position: int = 0


class WordLexeme(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("word_id", "lexeme_id"),)

    id: int | None = Field(default=None, primary_key=True)
    word_id: int = Field(foreign_key="word.id", index=True)
    lexeme_id: int = Field(foreign_key="lexeme.id", index=True)
    relation: str = "surface"


class SyncRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime
    completed_at: datetime | None = None
    status: str = "running"
    fetched_count: int = 0
    new_count: int = 0
    error_code: str | None = None
    error_message: str | None = None


class GenerationTask(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str
    status: str = "queued"
    total_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    current_session_id: int | None = None
    cancel_requested: bool = False
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class VocabularyTask(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    status: str = "queued"
    total_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    cancel_requested: bool = False
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CourseSkill(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("duolingo_skill_id"),)

    id: int | None = Field(default=None, primary_key=True)
    duolingo_skill_id: str = Field(index=True)
    title: str
    debug_name: str
    section_index: int
    unit_index: int
    path_level_index: int
    path_order: int = Field(index=True)
    current_level: int
    current_sessions: int
    total_sessions: int
    state: str
    created_at: datetime
    updated_at: datetime


class SkillSession(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("course_skill_id", "level_index", "session_index"),
    )

    id: int | None = Field(default=None, primary_key=True)
    course_skill_id: int = Field(foreign_key="courseskill.id", index=True)
    level_index: int
    session_index: int
    total_sessions: int
    path_order: int = Field(index=True)
    is_completed: bool = False
    synced_at: datetime | None = None
    word_count: int = 0
    completed_at: datetime | None = None


class SessionWord(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("skill_session_id", "word_id"),)

    id: int | None = Field(default=None, primary_key=True)
    skill_session_id: int = Field(foreign_key="skillsession.id", index=True)
    word_id: int = Field(foreign_key="word.id", index=True)
    position: int


class SessionLesson(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("skill_session_id"),)

    id: int | None = Field(default=None, primary_key=True)
    skill_session_id: int = Field(foreign_key="skillsession.id", index=True)
    created_at: datetime
    study_completed_at: datetime | None = None
    review_completed_at: datetime | None = None
    content_json: str | None = None
    content_version: int = 0
    content_generated_at: datetime | None = None
    outline_json: str | None = None
    outline_version: int = 0
    outline_generated_at: datetime | None = None
    generation_status: str = "pending"
    generation_error: str | None = None
