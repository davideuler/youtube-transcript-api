import logging
import os
import re
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse

from flask import Flask, jsonify, request

from youtube_transcript_api import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeRequestFailed,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig, InvalidProxyConfig

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = "8881"
DEFAULT_LANGUAGES = ["en"]
DEFAULT_LOG_FILE = "ytt_flask_api.log"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_video_id(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError("Field 'url' must not be empty.")

    if VIDEO_ID_PATTERN.fullmatch(candidate):
        return candidate

    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Field 'url' must be a YouTube video URL or a video ID.")

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
        if VIDEO_ID_PATTERN.fullmatch(video_id):
            return video_id

    if host.endswith("youtube.com"):
        if parsed.path == "/watch":
            video_ids = parse_qs(parsed.query).get("v", [])
            if video_ids and VIDEO_ID_PATTERN.fullmatch(video_ids[0]):
                return video_ids[0]

        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live"}:
            if VIDEO_ID_PATTERN.fullmatch(path_parts[1]):
                return path_parts[1]

    raise ValueError("Unable to extract a valid YouTube video ID from 'url'.")


def normalize_languages(value: Any) -> List[str]:
    if value is None:
        return list(DEFAULT_LANGUAGES)

    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, Sequence):
        raw_items = []
        for item in value:
            if isinstance(item, str):
                raw_items.extend(item.split(","))
            else:
                raise ValueError("Field 'languages' must contain only strings.")
    else:
        raise ValueError("Field 'languages' must be a string or a list of strings.")

    languages = []
    seen = set()
    for raw_item in raw_items:
        language_code = raw_item.strip()
        if not language_code or language_code in seen:
            continue
        seen.add(language_code)
        languages.append(language_code)

    if not languages:
        raise ValueError("Field 'languages' must contain at least one language code.")

    return languages


def create_app(config: Optional[Dict[str, Any]] = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        HTTP_PROXY_URL=os.getenv("YTA_PROXY_HTTP_URL"),
        HTTPS_PROXY_URL=os.getenv("YTA_PROXY_HTTPS_URL"),
        HOST=os.getenv("YTA_API_HOST", DEFAULT_HOST),
        PORT=os.getenv("YTA_API_PORT", DEFAULT_PORT),
        DEBUG=os.getenv("YTA_API_DEBUG", "false"),
        LOG_FILE=os.getenv("YTA_API_LOG_FILE", DEFAULT_LOG_FILE),
        LOG_LEVEL=os.getenv("YTA_API_LOG_LEVEL", "INFO"),
    )
    if config is not None:
        app.config.update(config)

    _configure_logging(app)

    @app.get("/healthz")
    def healthcheck():
        return jsonify({"status": "ok"})

    @app.route("/api/v1/transcripts", methods=["GET", "POST"])
    def transcripts():
        try:
            body = request.get_json(silent=True)
            if body is not None and not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object.")

            payload = body or {}
            url = _request_value("url", payload) or _request_value("video_url", payload)
            if url is None:
                raise ValueError("Missing required field: url.")

            languages = normalize_languages(_request_languages(payload))
            preserve_formatting = _to_bool(
                _request_value("preserve_formatting", payload, default=False)
            )

            result = fetch_transcriptions(
                video_url=url,
                languages=languages,
                preserve_formatting=preserve_formatting,
                proxy_config=_build_proxy_config(
                    app.config.get("HTTP_PROXY_URL"),
                    app.config.get("HTTPS_PROXY_URL"),
                ),
            )

            status_code = 200 if result["transcripts"] else 404
            if status_code == 404:
                app.logger.warning(
                    "transcripts 404 (no matching transcripts): "
                    "video_id=%s url=%s languages=%s errors=%s",
                    result.get("video_id"),
                    url,
                    languages,
                    result.get("errors"),
                )
            return jsonify(result), status_code
        except ValueError as error:
            return jsonify({"error": "BadRequest", "message": str(error)}), 400
        except InvalidProxyConfig as error:
            return jsonify({"error": "InvalidProxyConfig", "message": str(error)}), 500
        except CouldNotRetrieveTranscript as error:
            status = _status_code_for_exception(error)
            if status == 404:
                app.logger.warning(
                    "transcripts 404 (%s): url=%s message=%s",
                    error.__class__.__name__,
                    locals().get("url"),
                    str(error),
                )
            return (
                jsonify(
                    {
                        "error": error.__class__.__name__,
                        "message": str(error),
                    }
                ),
                status,
            )

    return app


def fetch_transcriptions(
    video_url: str,
    languages: Iterable[str],
    preserve_formatting: bool = False,
    proxy_config: Optional[GenericProxyConfig] = None,
) -> Dict[str, Any]:
    video_id = extract_video_id(video_url)
    transcript_list = YouTubeTranscriptApi(proxy_config=proxy_config).list(video_id)

    transcripts = []
    errors = []

    for language_code in languages:
        try:
            transcript, is_translated = _resolve_transcript(
                transcript_list, language_code
            )
            fetched_transcript = transcript.fetch(
                preserve_formatting=preserve_formatting
            )
            transcripts.append(
                {
                    "language": fetched_transcript.language,
                    "language_code": fetched_transcript.language_code,
                    "transcription": "\n".join(
                        snippet.text for snippet in fetched_transcript
                    ),
                    "is_generated": fetched_transcript.is_generated,
                    "is_translated": is_translated,
                }
            )
        except CouldNotRetrieveTranscript as error:
            errors.append(
                {
                    "language_code": language_code,
                    "error": error.__class__.__name__,
                    "message": str(error),
                }
            )

    return {
        "video_id": video_id,
        "transcripts": transcripts,
        "errors": errors,
    }


def _resolve_transcript(transcript_list, language_code: str) -> Tuple[Any, bool]:
    try:
        return transcript_list.find_transcript([language_code]), False
    except NoTranscriptFound as original_error:
        for transcript in transcript_list:
            if not getattr(transcript, "is_translatable", False):
                continue
            if _supports_translation(transcript, language_code):
                return transcript.translate(language_code), True
        raise original_error


def _supports_translation(transcript, language_code: str) -> bool:
    for translation_language in getattr(transcript, "translation_languages", []):
        if getattr(translation_language, "language_code", None) == language_code:
            return True
    return False


def _configure_logging(app: Flask) -> None:
    if app.config.get("TESTING"):
        return
    log_level = logging.getLevelName(str(app.config.get("LOG_LEVEL", "INFO")).upper())
    if not isinstance(log_level, int):
        log_level = logging.INFO
    app.logger.setLevel(log_level)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    has_stream = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        for h in app.logger.handlers
    )
    if not has_stream:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        app.logger.addHandler(stream_handler)

    log_file = app.config.get("LOG_FILE")
    if log_file and not any(
        isinstance(h, RotatingFileHandler) and getattr(h, "_ytt_log_file", None) == log_file
        for h in app.logger.handlers
    ):
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler._ytt_log_file = log_file  # type: ignore[attr-defined]
        app.logger.addHandler(file_handler)

    app.logger.propagate = False


def _build_proxy_config(
    http_proxy_url: Optional[str], https_proxy_url: Optional[str]
) -> Optional[GenericProxyConfig]:
    if not http_proxy_url and not https_proxy_url:
        return None
    return GenericProxyConfig(http_url=http_proxy_url, https_url=https_proxy_url)


def _request_value(name: str, payload: Dict[str, Any], default: Any = None) -> Any:
    if request.method == "GET":
        return request.args.get(name, default)
    return payload.get(name, default)


def _request_languages(payload: Dict[str, Any]) -> Any:
    if request.method == "GET":
        values = request.args.getlist("languages")
        if len(values) > 1:
            return values
        if len(values) == 1:
            return values[0]
        return None
    return payload.get("languages")


def _status_code_for_exception(error: CouldNotRetrieveTranscript) -> int:
    if isinstance(error, InvalidVideoId):
        return 400
    if isinstance(error, (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable)):
        return 404
    if isinstance(error, (RequestBlocked, IpBlocked)):
        return 429
    if isinstance(error, (AgeRestricted, VideoUnplayable, PoTokenRequired)):
        return 422
    if isinstance(error, YouTubeRequestFailed):
        return 502
    return 500


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError("Field 'preserve_formatting' must be a boolean.")


app = create_app()


def main():
    app.run(
        host=app.config["HOST"],
        port=int(app.config["PORT"]),
        debug=_to_bool(app.config["DEBUG"]),
    )
