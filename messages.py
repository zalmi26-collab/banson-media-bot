"""User-facing Hebrew message templates."""
from __future__ import annotations

from datetime import date


def fmt_size(bytes_: int | None) -> str:
    if not bytes_:
        return ""
    if bytes_ < 1024:
        return f"{bytes_} B"
    if bytes_ < 1024 * 1024:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_ / (1024 * 1024):.1f} MB"


def confirmation(
    folder_path_he: str,
    files: list[dict],
) -> str:
    """Confirmation prompt. Sent as a quoted reply to the first media in the
    session, so the user already sees which file (or files) it concerns. We
    only mention the count when there's more than one — the quote covers
    just the first, so the user needs to know how many are bundled."""
    header = "📦 לאשר העלאה?"
    if len(files) > 1:
        header += f"  ({len(files)} קבצים)"
    return (
        f"{header}\n"
        f"📁 {folder_path_he}\n"
        "\n"
        "👍 לאשר · שלח ניתוב חדש לתיקון · 'לא' לביטול"
    )


def upload_done_single(folder_path_he: str, link: str) -> str:
    return f"✅ הועלה\n📁 {folder_path_he}\n🔗 {link}"


def upload_done_bundle(folder_path_he: str, count: int) -> str:
    return f"✅ הועלו {count} קבצים\n📁 {folder_path_he}"


def upload_partial_failure(folder_path_he: str, ok: int, total: int, errors: list[str]) -> str:
    body = f"⚠️ הועלו {ok}/{total} קבצים\n📁 {folder_path_he}"
    if errors:
        body += "\n\nשגיאות:\n" + "\n".join(f"• {e}" for e in errors[:3])
    return body


def cancelled() -> str:
    return "🚫 בוטל. שלח שוב עם הניתוב הנכון."


def no_active_session() -> str:
    return (
        "🤔 לא הבנתי. אני מחכה לקובץ עם ניתוב בפורמט:\n"
        "מגרש/בניין/דירה/שלב — למשל 1096/6/1/18"
    )


def parse_error() -> str:
    return (
        "⚠️ פורמט לא תקין.\n"
        "שלח: {מגרש}/{בניין}/{דירה}/{שלב}\n"
        "למשל: 1096/6/1/18"
    )


def folder_not_found(level: str, value: str | int) -> str:
    return f"❌ {level} {value} לא נמצא ב-Drive. שלח ניתוב מתוקן."


def need_destination() -> str:
    return (
        "📥 קיבלתי קובץ. לאיזה יעד?\n"
        "שלח ניתוב בפורמט מגרש/בניין/דירה/שלב — למשל 1096/6/1/18"
    )


def upload_started(count: int) -> str:
    if count == 1:
        return "⏳ מעלה..."
    return f"⏳ מעלה {count} קבצים..."
