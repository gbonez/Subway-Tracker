"""SMS helpers backed by Textbelt."""

import os

import requests


TEXTBELT_ENDPOINT = "https://textbelt.com/text"
TEXTBELT_API_KEY = os.environ.get("TEXTBELT_API_KEY", "")


def send_text_message(phone_number: str, message_body: str, api_key: str | None = None, timeout: int = 10) -> dict:
    resolved_api_key = api_key or TEXTBELT_API_KEY
    if not resolved_api_key or not phone_number:
        raise ValueError("Missing phone number or TEXTBELT_API_KEY")

    response = requests.post(
        TEXTBELT_ENDPOINT,
        data={
            "phone": phone_number,
            "message": message_body,
            "key": resolved_api_key,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()