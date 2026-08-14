from openai import OpenAI

from app.core.config import settings


def build_inventory_context(items: list[dict]) -> str:
    if not items:
        return "Inventory is empty."
    summary = []
    for item in items:
        unit = item.get("unit") or "count"
        quantity = item.get("quantity")
        if unit == "count":
            qty_text = f"x{quantity}"
        else:
            qty_text = f"{quantity} {unit}"
        line = f"- {item['name']} {qty_text}"
        if item.get("expiration_date"):
            line += f" (expires {item['expiration_date']})"
        summary.append(line)
    return "\n".join(summary)


def generate_chat_reply(message: str, inventory_context: str) -> str:
    if not settings.openai_api_key:
        return "OpenAI API key not configured."
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful grocery assistant. "
                    "Use the inventory context to suggest recipes or storage tips."
                    "If the user asks about something that is not food related, politely answer in brief if you know the answer but mention that you are a grocery assistant and suggest them to ask about food related questions."
                ),
            },
            {"role": "system", "content": f"Inventory:\n{inventory_context}"},
            {"role": "user", "content": message},
        ],
        max_tokens=300,
    )
    return response.choices[0].message.content.strip()
