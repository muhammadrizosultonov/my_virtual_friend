import html
from typing import Any, Dict, List
from datetime import datetime


def prepare_daily_transcript(messages: List[Dict[str, Any]]) -> str:
    """
    Xabarlarni kontaktlar bo'yicha guruhlab, Gemini AI o'qishi uchun 
    toza va tartibli suhbat matnini shakllantiradi.
    """
    if not messages:
        return ""

    grouped_chats: Dict[int, Dict[str, Any]] = {}
    for msg in messages:
        chat_id = msg["chat_id"]
        if chat_id not in grouped_chats:
            grouped_chats[chat_id] = {
                "name": msg["sender_name"] or "Noma'lum",
                "username": msg["username"],
                "messages": []
            }
        grouped_chats[chat_id]["messages"].append(msg)

    transcript_parts = []
    for chat_id, chat_data in grouped_chats.items():
        name = chat_data["name"]
        username = f" (@{chat_data['username']})" if chat_data["username"] else ""
        header = f"👤 KONTAKT: {name}{username} [ID: {chat_id}]"
        
        chat_lines = [header, "-" * len(header)]
        for m in chat_data["messages"]:
            sender_label = "Muhammadrizo (Men)" if m["is_outgoing"] else name
            time_str = m["created_at"].split(" ")[1][:5] if " " in m["created_at"] else ""
            prefix = f"[{time_str}] " if time_str else ""
            chat_lines.append(f"{prefix}{sender_label}: {m['text']}")

        transcript_parts.append("\n".join(chat_lines))

    return "\n\n" + ("\n" + "=" * 50 + "\n\n").join(transcript_parts)


def format_stats_header(
    stats_today: Dict[str, Any],
    stats_7days: List[Dict[str, Any]],
    stats_month: List[Dict[str, Any]],
    date_str: str
) -> str:
    """
    Matematik statistikani Telegram uchun chiroyli HTML formatida shakllantirish.
    """
    total = stats_today.get("total_messages", 0)
    incoming = stats_today.get("incoming_count", 0)
    outgoing = stats_today.get("outgoing_count", 0)
    unique_contacts = stats_today.get("unique_contacts", 0)

    lines = [
        f"📋 <b>KUNLIK MULOQOT VA SAMARADORLIK HISOBOTI</b>",
        f"📅 <i>Sana: {date_str}</i>\n",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>MATEMATIK STATISTIKA:</b>\n",
        f"👥 <b>Gaplashilgan kontaktlar:</b> {unique_contacts} ta",
        f"✉️ <b>Jami xabarlar soni:</b> {total} ta",
        f"   • 📥 Kiruvchi: <b>{incoming}</b> ta",
        f"   • 📤 Chiquvchi: <b>{outgoing}</b> ta\n"
    ]

    # Bugungi TOP kontaktlar
    top_today = stats_today.get("top_contacts_today", [])
    if top_today:
        lines.append("🏆 <b>Bugungi TOP suhbatdoshlar:</b>")
        for idx, contact in enumerate(top_today[:5], 1):
            name = html.escape(contact["name"] or "Noma'lum")
            uname = f" (@{html.escape(contact['username'])})" if contact["username"] else ""
            cnt = contact["message_count"]
            inc = contact["incoming"]
            out = contact["outgoing"]
            lines.append(f"{idx}. <b>{name}</b>{uname} — {cnt} ta xabar (📥 {inc} | 📤 {out})")
        lines.append("")

    # 7 kunlik TOP kontaktlar
    if stats_7days:
        lines.append("📈 <b>Oxirgi 7 kunlik eng faol kontaktlar:</b>")
        for idx, contact in enumerate(stats_7days[:3], 1):
            name = html.escape(contact["name"] or "Noma'lum")
            uname = f" (@{html.escape(contact['username'])})" if contact["username"] else ""
            cnt = contact["total_count"]
            lines.append(f"{idx}. <b>{name}</b>{uname} — {cnt} ta xabar")
        lines.append("")

    # Oylik (30 kunlik) TOP kontaktlar
    if stats_month:
        lines.append("🗓 <b>Oxirgi 30 kunlik (Oylik) eng faol kontaktlar:</b>")
        for idx, contact in enumerate(stats_month[:3], 1):
            name = html.escape(contact["name"] or "Noma'lum")
            uname = f" (@{html.escape(contact['username'])})" if contact["username"] else ""
            cnt = contact["total_count"]
            lines.append(f"{idx}. <b>{name}</b>{uname} — {cnt} ta xabar")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

