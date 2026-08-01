import logging

import requests
from django.conf import settings
from django.core.mail import send_mail


logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Raised when a transactional email cannot be delivered."""


def _email_provider():
    provider = getattr(settings, "TRANSACTIONAL_EMAIL_PROVIDER", "auto").lower()
    if provider == "auto":
        return "resend" if getattr(settings, "RESEND_API_KEY", "") else "django"
    return provider


def send_transactional_email(*, subject, message, recipient):
    """Send account email through Resend HTTPS or the local Django backend."""

    provider = _email_provider()
    if provider == "django":
        try:
            return send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
        except Exception as exc:
            logger.exception("Django email backend failed for recipient domain=%s", _domain(recipient))
            raise EmailDeliveryError("Django email backend failed.") from exc

    if provider != "resend":
        raise EmailDeliveryError(f"Unsupported email provider: {provider}")

    api_key = getattr(settings, "RESEND_API_KEY", "").strip()
    from_email = getattr(settings, "RESEND_FROM_EMAIL", "").strip()
    if not api_key or not from_email:
        raise EmailDeliveryError("RESEND_API_KEY or RESEND_FROM_EMAIL is not configured.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Resend rejects direct HTTP requests without a User-Agent.
        "User-Agent": "XHcode/AnimeDashboard/1.0 (Django)",
    }
    payload = {
        "from": from_email,
        "to": [recipient],
        "subject": subject,
        "text": message,
    }
    timeout = (
        getattr(settings, "RESEND_CONNECT_TIMEOUT", 3.05),
        getattr(settings, "RESEND_READ_TIMEOUT", 8.0),
    )

    try:
        response = requests.post(
            settings.RESEND_API_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        logger.warning(
            "Resend API timed out for recipient domain=%s (connect=%ss, read=%ss)",
            _domain(recipient),
            timeout[0],
            timeout[1],
        )
        raise EmailDeliveryError("Resend API timed out.") from exc
    except requests.RequestException as exc:
        logger.exception("Resend API request failed for recipient domain=%s", _domain(recipient))
        raise EmailDeliveryError("Resend API request failed.") from exc

    if not 200 <= response.status_code < 300:
        logger.error(
            "Resend API rejected email: status=%s recipient_domain=%s response=%s",
            response.status_code,
            _domain(recipient),
            response.text[:1000],
        )
        raise EmailDeliveryError(f"Resend API returned HTTP {response.status_code}.")

    try:
        response_data = response.json()
    except ValueError as exc:
        logger.error("Resend API returned invalid JSON: %s", response.text[:1000])
        raise EmailDeliveryError("Resend API returned invalid JSON.") from exc

    message_id = response_data.get("id")
    if not message_id:
        logger.error("Resend API response did not contain a message id: %s", response_data)
        raise EmailDeliveryError("Resend API response did not contain a message id.")

    logger.info(
        "Resend accepted email id=%s recipient_domain=%s",
        message_id,
        _domain(recipient),
    )
    return message_id


def _domain(email):
    return email.rsplit("@", 1)[-1].lower() if "@" in email else "invalid"
