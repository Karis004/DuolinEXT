import json
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from .ai import VOCABULARY_PROMPT_VERSION, generate_vocabulary_profiles
from .config import Settings
from .models import Lexeme, LexemeForm, VocabularyTask, Word, WordLexeme
from .session_service import now, serialize_word


VOCABULARY_TASK_STALE_AFTER = timedelta(minutes=3)


def queue_vocabulary_words(session: Session, settings: Settings) -> list[int]:
    queued: list[int] = []
    for word in session.exec(select(Word).order_by(col(Word.id))).all():
        links = session.exec(
            select(Lexeme)
            .join(WordLexeme, col(WordLexeme.lexeme_id) == col(Lexeme.id))
            .where(WordLexeme.word_id == word.id)
        ).all()
        if any(
            lexeme.generation_status == "ready"
            and lexeme.generation_version >= VOCABULARY_PROMPT_VERSION
            for lexeme in links
        ):
            continue
        if links and all(
            lexeme.generation_status == "error"
            and lexeme.generation_version >= VOCABULARY_PROMPT_VERSION
            for lexeme in links
        ):
            continue
        placeholder = links[0] if links else Lexeme(
            lemma=word.text,
            part_of_speech="其他",
            generation_status="pending",
            updated_at=now(settings),
        )
        if not links:
            session.add(placeholder)
            session.flush()
            session.add(WordLexeme(word_id=word.id, lexeme_id=placeholder.id))
        if placeholder.generation_status == "generating":
            continue
        if placeholder.generation_status != "queued":
            placeholder.generation_status = "queued"
            placeholder.updated_at = now(settings)
            session.add(placeholder)
        queued.append(word.id)
    session.commit()
    return queued


def _store_profile(
    session: Session,
    settings: Settings,
    word: Word,
    profile: dict,
) -> None:
    lemma = str(profile.get("lemma") or word.text).strip()
    part_of_speech = str(profile.get("partOfSpeech") or "其他").strip()
    lexeme = session.exec(
        select(Lexeme).where(
            Lexeme.lemma == lemma,
            Lexeme.part_of_speech == part_of_speech,
        )
    ).first()
    if lexeme is None:
        lexeme = Lexeme(lemma=lemma, part_of_speech=part_of_speech)
        session.add(lexeme)
        session.flush()

    old_links = session.exec(
        select(WordLexeme).where(WordLexeme.word_id == word.id)
    ).all()
    for link in old_links:
        if link.lexeme_id != lexeme.id:
            session.delete(link)
    if not any(link.lexeme_id == lexeme.id for link in old_links):
        session.add(WordLexeme(word_id=word.id, lexeme_id=lexeme.id))

    old_forms = session.exec(
        select(LexemeForm).where(LexemeForm.lexeme_id == lexeme.id)
    ).all()
    for form in old_forms:
        session.delete(form)
    session.flush()
    forms = profile.get("forms") if isinstance(profile.get("forms"), list) else []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(forms[:8]):
        if not isinstance(item, dict):
            continue
        form = str(item.get("form") or "").strip()
        label = str(item.get("label") or "核心词形").strip()
        if not form or (form, label) in seen:
            continue
        seen.add((form, label))
        session.add(
            LexemeForm(
                lexeme_id=lexeme.id,
                form=form,
                label=label,
                note=str(item.get("note") or "").strip(),
                position=index,
            )
        )
    lexeme.gender = str(profile.get("gender") or "").strip() or None
    lexeme.meaning = str(profile.get("meaning") or "").strip()
    lexeme.usage_note = str(profile.get("usageNote") or "").strip()
    lexeme.generation_status = "ready"
    lexeme.generation_version = VOCABULARY_PROMPT_VERSION
    lexeme.generated_at = now(settings)
    lexeme.updated_at = now(settings)
    session.add(lexeme)


def run_vocabulary_batch(
    word_ids: list[int], settings: Settings, task_id: int | None = None
) -> None:
    from .database import engine

    with Session(engine) as session:
        task = session.get(VocabularyTask, task_id) if task_id else None
        if task is not None:
            task.status = "running"
            task.started_at = task.started_at or now(settings)
            session.add(task)
            session.commit()
        words = session.exec(
            select(Word).where(col(Word.id).in_(word_ids)).order_by(col(Word.id))
        ).all()
        chunk_size = 4
        for offset in range(0, len(words), chunk_size):
            if task is not None:
                session.refresh(task)
                if task.cancel_requested:
                    task.status = "cancelled"
                    task.finished_at = now(settings)
                    session.add(task)
                    session.commit()
                    return
            chunk = words[offset : offset + chunk_size]
            try:
                profiles = generate_vocabulary_profiles(
                    settings, [serialize_word(word) for word in chunk]
                )
                by_word = {
                    str(item.get("word") or "").casefold(): item
                    for item in profiles
                    if isinstance(item, dict)
                }
                for word in chunk:
                    profile = by_word.get(word.text.casefold())
                    if profile is not None:
                        _store_profile(session, settings, word, profile)
                    else:
                        _mark_word_error(session, word, settings, "AI 未返回该词的词形资料。")
                session.commit()
            except Exception as exc:
                session.rollback()
                for word in chunk:
                    _mark_word_error(session, word, settings, str(exc))
                session.commit()
            if task is not None:
                task.completed_count += len(chunk)
                task.failed_count += sum(
                    1
                    for word in chunk
                    if (
                        lexeme := session.exec(
                            select(Lexeme)
                            .join(WordLexeme, col(WordLexeme.lexeme_id) == col(Lexeme.id))
                            .where(WordLexeme.word_id == word.id)
                        ).first()
                    ) is not None
                    and lexeme.generation_status == "error"
                )
                session.add(task)
                session.commit()
        if task is not None:
            task.status = "partial" if task.failed_count else "completed"
            task.finished_at = now(settings)
            session.add(task)
            session.commit()


def _mark_word_error(session: Session, word: Word, settings: Settings, message: str) -> None:
    lexeme = session.exec(
        select(Lexeme)
        .join(WordLexeme, col(WordLexeme.lexeme_id) == col(Lexeme.id))
        .where(WordLexeme.word_id == word.id)
    ).first()
    if lexeme is None:
        lexeme = Lexeme(lemma=word.text)
    lexeme.generation_status = "error"
    lexeme.generation_version = VOCABULARY_PROMPT_VERSION
    lexeme.usage_note = message[:240]
    lexeme.updated_at = now(settings)
    session.add(lexeme)


def vocabulary_pending_count(session: Session) -> int:
    words = session.exec(select(Word)).all()
    ready_word_ids = set(
        session.exec(
            select(WordLexeme.word_id)
            .join(Lexeme, col(Lexeme.id) == col(WordLexeme.lexeme_id))
            .where(
                Lexeme.generation_status == "ready",
                Lexeme.generation_version >= VOCABULARY_PROMPT_VERSION,
            )
        ).all()
    )
    return sum(word.id not in ready_word_ids for word in words)


def active_vocabulary_task(session: Session) -> VocabularyTask | None:
    task = session.exec(
        select(VocabularyTask)
        .where(VocabularyTask.status.in_(["queued", "running"]))
        .order_by(col(VocabularyTask.id).desc())
    ).first()
    if (
        task is not None
        and task.status == "running"
        and task.started_at is not None
        and datetime.now() - task.started_at.replace(tzinfo=None)
        > VOCABULARY_TASK_STALE_AFTER
    ):
        task.status = "cancelled"
        task.cancel_requested = True
        task.finished_at = datetime.now()
        task.error_message = "词形整理超过 3 分钟没有进度，任务已自动停止。"
        session.add(task)
        session.commit()
        return None
    return task


def create_vocabulary_task(
    session: Session, settings: Settings, word_ids: list[int]
) -> VocabularyTask:
    active = active_vocabulary_task(session)
    if active is not None:
        return active
    task = VocabularyTask(
        total_count=len(word_ids),
        created_at=now(settings),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def vocabulary_payload(session: Session) -> list[dict]:
    result = []
    for word in session.exec(select(Word).order_by(col(Word.text))).all():
        lexeme = session.exec(
            select(Lexeme)
            .join(WordLexeme, col(WordLexeme.lexeme_id) == col(Lexeme.id))
            .where(WordLexeme.word_id == word.id)
            .order_by(col(Lexeme.generation_version).desc())
        ).first()
        if lexeme is None:
            result.append(
                {
                    "word": word.text,
                    "translations": json.loads(word.translations_json),
                    "lemma": word.text,
                    "partOfSpeech": "其他",
                    "gender": None,
                    "meaning": "",
                    "usageNote": "",
                    "status": "pending",
                    "forms": [],
                }
            )
            continue
        forms = session.exec(
            select(LexemeForm)
            .where(LexemeForm.lexeme_id == lexeme.id)
            .order_by(col(LexemeForm.position))
        ).all()
        result.append(
            {
                "word": word.text,
                "translations": json.loads(word.translations_json),
                "lemma": lexeme.lemma,
                "partOfSpeech": lexeme.part_of_speech,
                "gender": lexeme.gender,
                "meaning": lexeme.meaning,
                "usageNote": lexeme.usage_note,
                "status": lexeme.generation_status,
                "forms": [
                    {"form": item.form, "label": item.label, "note": item.note}
                    for item in forms
                ],
            }
        )
    return result
