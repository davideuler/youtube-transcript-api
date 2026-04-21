from unittest import TestCase
from unittest.mock import patch

from requests import HTTPError

from youtube_transcript_api import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    FetchedTranscript,
    FetchedTranscriptSnippet,
    InvalidVideoId,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeRequestFailed,
)
from youtube_transcript_api.proxies import InvalidProxyConfig

from ytt_flask_api.app import (
    _status_code_for_exception,
    _to_bool,
    create_app,
    extract_video_id,
    main,
    normalize_languages,
)


class FakeTranslationLanguage:
    def __init__(self, language: str, language_code: str):
        self.language = language
        self.language_code = language_code


class FakeTranscript:
    def __init__(
        self,
        video_id: str,
        language: str,
        language_code: str,
        text: str,
        is_generated: bool = False,
        translation_languages=None,
        translated_transcripts=None,
    ):
        self.video_id = video_id
        self.language = language
        self.language_code = language_code
        self._text = text
        self.is_generated = is_generated
        self.translation_languages = translation_languages or []
        self._translated_transcripts = translated_transcripts or {}

    @property
    def is_translatable(self):
        return bool(self.translation_languages)

    def fetch(self, preserve_formatting: bool = False):
        text = (
            self._text
            if preserve_formatting
            else self._text.replace("<i>", "").replace("</i>", "")
        )
        return FetchedTranscript(
            snippets=[
                FetchedTranscriptSnippet(
                    text=text,
                    start=0.0,
                    duration=1.0,
                )
            ],
            video_id=self.video_id,
            language=self.language,
            language_code=self.language_code,
            is_generated=self.is_generated,
        )

    def translate(self, language_code: str):
        return self._translated_transcripts[language_code]


class FakeTranscriptList:
    def __init__(self, video_id: str, transcripts):
        self.video_id = video_id
        self._transcripts = transcripts

    def __iter__(self):
        return iter(self._transcripts.values())

    def find_transcript(self, language_codes):
        for language_code in language_codes:
            transcript = self._transcripts.get(language_code)
            if transcript is not None:
                return transcript
        raise NoTranscriptFound(self.video_id, language_codes, self)

    def __str__(self):
        return "fake transcript list"


class TestExtractVideoId(TestCase):
    def test_extract_video_id__watch_url(self):
        self.assertEqual(
            extract_video_id("https://www.youtube.com/watch?v=GJLlxj_dtq8"),
            "GJLlxj_dtq8",
        )

    def test_extract_video_id__shorts_url(self):
        self.assertEqual(
            extract_video_id("https://www.youtube.com/shorts/GJLlxj_dtq8"),
            "GJLlxj_dtq8",
        )

    def test_extract_video_id__raw_id(self):
        self.assertEqual(extract_video_id("GJLlxj_dtq8"), "GJLlxj_dtq8")

    def test_extract_video_id__empty_value(self):
        with self.assertRaises(ValueError):
            extract_video_id("")

    def test_extract_video_id__invalid_url(self):
        with self.assertRaises(ValueError):
            extract_video_id("https://example.com/watch?v=GJLlxj_dtq8")


class TestNormalizeLanguages(TestCase):
    def test_normalize_languages__defaults_to_english(self):
        self.assertEqual(normalize_languages(None), ["en"])

    def test_normalize_languages__deduplicates_and_skips_empty_items(self):
        self.assertEqual(normalize_languages(["en, zh", "en", ""]), ["en", "zh"])

    def test_normalize_languages__rejects_non_string_items(self):
        with self.assertRaises(ValueError):
            normalize_languages(["en", 123])

    def test_normalize_languages__rejects_invalid_type(self):
        with self.assertRaises(ValueError):
            normalize_languages({"en"})

    def test_normalize_languages__rejects_empty_language_list(self):
        with self.assertRaises(ValueError):
            normalize_languages(" , ")


class TestRestApi(TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()
        self.transcript_list = self._build_transcript_list()

    def _build_transcript_list(self):
        video_id = "GJLlxj_dtq8"
        translated_zh = FakeTranscript(
            video_id=video_id,
            language="Chinese (Simplified)",
            language_code="zh",
            text="你好，世界",
            is_generated=True,
        )
        english = FakeTranscript(
            video_id=video_id,
            language="English",
            language_code="en",
            text="Hello <i>world</i>",
            translation_languages=[
                FakeTranslationLanguage(
                    language="Chinese (Simplified)",
                    language_code="zh",
                )
            ],
            translated_transcripts={"zh": translated_zh},
        )
        return FakeTranscriptList(video_id, {"en": english})

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_post_transcripts__returns_direct_and_translated_results(self, api_cls):
        api_cls.return_value.list.return_value = self.transcript_list

        response = self.client.post(
            "/api/v1/transcripts",
            json={
                "url": "https://www.youtube.com/watch?v=GJLlxj_dtq8",
                "languages": "en,zh",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["video_id"], "GJLlxj_dtq8")
        self.assertEqual(payload["errors"], [])
        self.assertEqual(
            payload["transcripts"],
            [
                {
                    "language": "English",
                    "language_code": "en",
                    "transcription": "Hello world",
                    "is_generated": False,
                    "is_translated": False,
                },
                {
                    "language": "Chinese (Simplified)",
                    "language_code": "zh",
                    "transcription": "你好，世界",
                    "is_generated": True,
                    "is_translated": True,
                },
            ],
        )

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_post_transcripts__returns_partial_errors(self, api_cls):
        api_cls.return_value.list.return_value = self.transcript_list

        response = self.client.post(
            "/api/v1/transcripts",
            json={
                "url": "https://youtu.be/GJLlxj_dtq8",
                "languages": ["en", "fr"],
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["transcripts"]), 1)
        self.assertEqual(len(payload["errors"]), 1)
        self.assertEqual(payload["errors"][0]["language_code"], "fr")
        self.assertEqual(payload["errors"][0]["error"], "NoTranscriptFound")

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_post_transcripts__returns_404_when_nothing_matches(self, api_cls):
        api_cls.return_value.list.return_value = self.transcript_list

        response = self.client.post(
            "/api/v1/transcripts",
            json={
                "url": "https://youtu.be/GJLlxj_dtq8",
                "languages": "fr",
            },
        )

        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        self.assertEqual(payload["transcripts"], [])
        self.assertEqual(len(payload["errors"]), 1)

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_get_transcripts__supports_multiple_language_query_values(self, api_cls):
        api_cls.return_value.list.return_value = self.transcript_list

        response = self.client.get(
            "/api/v1/transcripts",
            query_string=[
                ("url", "https://youtu.be/GJLlxj_dtq8"),
                ("languages", "en"),
                ("languages", "zh"),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            [item["language_code"] for item in payload["transcripts"]],
            ["en", "zh"],
        )

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_get_transcripts__builds_generic_proxy_config(self, api_cls):
        app = create_app(
            {
                "TESTING": True,
                "HTTP_PROXY_URL": "http://proxy.local:8080",
                "HTTPS_PROXY_URL": "https://proxy.local:8443",
            }
        )
        client = app.test_client()
        api_cls.return_value.list.return_value = self.transcript_list

        response = client.get(
            "/api/v1/transcripts",
            query_string={
                "url": "https://youtu.be/GJLlxj_dtq8",
                "languages": "en",
            },
        )

        self.assertEqual(response.status_code, 200)
        proxy_config = api_cls.call_args.kwargs["proxy_config"]
        self.assertEqual(proxy_config.http_url, "http://proxy.local:8080")
        self.assertEqual(proxy_config.https_url, "https://proxy.local:8443")

    def test_healthcheck(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_post_transcripts__validates_bad_requests(self):
        response = self.client.post(
            "/api/v1/transcripts",
            json={"url": "not-a-youtube-url", "languages": "en"},
        )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error"], "BadRequest")

    def test_post_transcripts__validates_missing_url(self):
        response = self.client.post("/api/v1/transcripts", json={"languages": "en"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "BadRequest")

    def test_post_transcripts__validates_non_object_json(self):
        response = self.client.post("/api/v1/transcripts", json=["bad-payload"])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "BadRequest")

    @patch("ytt_flask_api.app._build_proxy_config")
    def test_post_transcripts__handles_invalid_proxy_config(self, build_proxy_config):
        build_proxy_config.side_effect = InvalidProxyConfig("broken proxy")

        response = self.client.post(
            "/api/v1/transcripts",
            json={
                "url": "https://www.youtube.com/watch?v=GJLlxj_dtq8",
                "languages": "en",
            },
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "InvalidProxyConfig")

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_post_transcripts__preserves_formatting_when_requested(self, api_cls):
        api_cls.return_value.list.return_value = self.transcript_list

        response = self.client.post(
            "/api/v1/transcripts",
            json={
                "url": "https://www.youtube.com/watch?v=GJLlxj_dtq8",
                "languages": "en",
                "preserve_formatting": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["transcripts"][0]["transcription"],
            "Hello <i>world</i>",
        )

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_post_transcripts__validates_preserve_formatting(self, api_cls):
        api_cls.return_value.list.return_value = self.transcript_list

        response = self.client.post(
            "/api/v1/transcripts",
            json={
                "url": "https://www.youtube.com/watch?v=GJLlxj_dtq8",
                "languages": "en",
                "preserve_formatting": "maybe",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "BadRequest")

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_get_transcripts__defaults_languages_to_english(self, api_cls):
        api_cls.return_value.list.return_value = self.transcript_list

        response = self.client.get(
            "/api/v1/transcripts",
            query_string={"url": "https://youtu.be/GJLlxj_dtq8"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["transcripts"][0]["language_code"],
            "en",
        )

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_post_transcripts__maps_top_level_transcript_errors(self, api_cls):
        api_cls.return_value.list.side_effect = VideoUnavailable("GJLlxj_dtq8")

        response = self.client.post(
            "/api/v1/transcripts",
            json={
                "url": "https://youtu.be/GJLlxj_dtq8",
                "languages": "en",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "VideoUnavailable")

    @patch("ytt_flask_api.app.YouTubeTranscriptApi")
    def test_translation_lookup__skips_non_translatable_transcript(self, api_cls):
        translated_zh = FakeTranscript(
            video_id="GJLlxj_dtq8",
            language="Chinese (Simplified)",
            language_code="zh",
            text="你好，世界",
            is_generated=True,
        )
        transcript_list = FakeTranscriptList(
            "GJLlxj_dtq8",
            {
                "es": FakeTranscript(
                    video_id="GJLlxj_dtq8",
                    language="Spanish",
                    language_code="es",
                    text="Hola mundo",
                ),
                "en": FakeTranscript(
                    video_id="GJLlxj_dtq8",
                    language="English",
                    language_code="en",
                    text="Hello world",
                    translation_languages=[
                        FakeTranslationLanguage(
                            language="Chinese (Simplified)",
                            language_code="zh",
                        )
                    ],
                    translated_transcripts={"zh": translated_zh},
                ),
            },
        )
        api_cls.return_value.list.return_value = transcript_list

        response = self.client.post(
            "/api/v1/transcripts",
            json={
                "url": "https://youtu.be/GJLlxj_dtq8",
                "languages": "zh",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["transcripts"][0]["language_code"],
            "zh",
        )


class TestRestApiHelpers(TestCase):
    def test_status_code_mapping(self):
        self.assertEqual(_status_code_for_exception(InvalidVideoId("video_id")), 400)
        self.assertEqual(
            _status_code_for_exception(TranscriptsDisabled("video_id")), 404
        )
        self.assertEqual(_status_code_for_exception(VideoUnavailable("video_id")), 404)
        self.assertEqual(_status_code_for_exception(RequestBlocked("video_id")), 429)
        self.assertEqual(_status_code_for_exception(AgeRestricted("video_id")), 422)
        self.assertEqual(
            _status_code_for_exception(
                YouTubeRequestFailed("video_id", HTTPError("boom"))
            ),
            502,
        )
        self.assertEqual(
            _status_code_for_exception(CouldNotRetrieveTranscript("video_id")),
            500,
        )

    def test_to_bool(self):
        self.assertTrue(_to_bool(True))
        self.assertFalse(_to_bool(None))
        self.assertTrue(_to_bool("true"))
        self.assertFalse(_to_bool("off"))

        with self.assertRaises(ValueError):
            _to_bool("maybe")

    @patch("ytt_flask_api.app.app.run")
    def test_main_runs_flask_app(self, run_mock):
        with patch.dict(
            "ytt_flask_api.app.app.config",
            {"HOST": "127.0.0.1", "PORT": "8080", "DEBUG": "false"},
            clear=False,
        ):
            main()

        run_mock.assert_called_once_with(host="127.0.0.1", port=8080, debug=False)
