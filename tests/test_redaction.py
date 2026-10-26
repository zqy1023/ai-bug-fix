"""运行输出与 Git 参数的敏感信息处理。"""

from __future__ import annotations

import unittest


class RedactForDeliveryTests(unittest.TestCase):
    """交付文本只隐藏明确凭据，普通文本原样保留。"""

    def test_url_userinfo_masked(self):
        from app.infrastructure.redaction import redact_for_delivery

        self.assertEqual(
            redact_for_delivery("clone https://user:pass@example.com/repo.git"),
            "clone https://***@example.com/repo.git",
        )

    def test_auth_header_masked(self):
        from app.infrastructure.redaction import redact_for_delivery

        self.assertEqual(
            redact_for_delivery("Authorization: Bearer abc.def-ghi_jkl"),
            "Authorization: ***",
        )
        self.assertEqual(
            redact_for_delivery("authorization: Basic dXNlcjpwYXNz"),
            "authorization: ***",
        )
        self.assertEqual(
            redact_for_delivery("Proxy-Authorization: token xyz"),
            "Proxy-Authorization: ***",
        )

    def test_cookie_header_masked(self):
        from app.infrastructure.redaction import redact_for_delivery

        self.assertEqual(
            redact_for_delivery("Cookie: session=abc; theme=dark"),
            "Cookie: ***",
        )

    def test_query_token_masked(self):
        from app.infrastructure.redaction import redact_for_delivery

        self.assertEqual(
            redact_for_delivery(
                "https://api.example.com/x?access_token=abc123&foo=bar"
            ),
            "https://api.example.com/x?access_token=***&foo=bar",
        )
        self.assertEqual(
            redact_for_delivery("https://api.example.com/x?token=abc123"),
            "https://api.example.com/x?token=***",
        )

    def test_aws_style_long_key_masked(self):
        from app.infrastructure.redaction import redact_for_delivery

        self.assertEqual(
            redact_for_delivery(
                "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
            ),
            "aws_secret_access_key=***",
        )

    def test_plain_text_unchanged(self):
        from app.infrastructure.redaction import redact_for_delivery

        text = "修复了 gathers/Test/main.py 的 JSON 解析错误，单号 888001 未命中缓存"
        self.assertEqual(redact_for_delivery(text), text)

    def test_normal_url_without_credentials_unchanged(self):
        from app.infrastructure.redaction import redact_for_delivery

        text = "GET https://api.example.com/v1/track?number=888001 HTTP/1.1"
        self.assertEqual(redact_for_delivery(text), text)

    def test_empty_and_non_string_input(self):
        from app.infrastructure.redaction import redact_for_delivery

        self.assertEqual(redact_for_delivery(""), "")
        self.assertEqual(redact_for_delivery(None), "")

    def test_json_quoted_headers_masked(self):
        """JSON 形态的鉴权头/cookie 之前因引号夹在中间而漏出。"""
        from app.infrastructure.redaction import redact_for_delivery

        self.assertEqual(
            redact_for_delivery('{"Authorization": "Bearer sk-abc123"}'),
            '{"Authorization": "***"}',
        )
        self.assertEqual(
            redact_for_delivery('{"Cookie": "session=deadbeef"}'),
            '{"Cookie": "***"}',
        )

    def test_password_assignment_masked(self):
        from app.infrastructure.redaction import redact_for_delivery

        self.assertEqual(redact_for_delivery("password=hunter2"), "password=***")
        self.assertEqual(redact_for_delivery("passwd: hunter2"), "passwd: ***")
        self.assertEqual(redact_for_delivery("password: hunter2"), "password: ***")
        self.assertEqual(
            redact_for_delivery('{"password": "hunter2"}'),
            '{"password": "***"}',
        )
        self.assertEqual(
            redact_for_delivery('client_secret="xyz123"'),
            'client_secret="***"',
        )

    def test_pem_private_key_block_masked(self):
        from app.infrastructure.redaction import redact_for_delivery

        block = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA1234\n"
            "-----END RSA PRIVATE KEY-----"
        )
        masked = redact_for_delivery(block)
        self.assertNotIn("MIIEowIBAAKCAQEA1234", masked)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", masked)

    def test_bare_aws_key_id_masked(self):
        from app.infrastructure.redaction import redact_for_delivery

        self.assertEqual(
            redact_for_delivery("id=AKIAIOSFODNN7EXAMPLE"),
            "id=***",
        )

    def test_prose_with_secret_word_unchanged(self):
        """secret/token 也可能是正文用语，不要把说明文字抹掉。"""
        from app.infrastructure.redaction import redact_for_delivery

        text = "secret: 已轮换，无需处理；token 由网关侧签发"
        self.assertEqual(redact_for_delivery(text), text)


class SourceRedactTests(unittest.TestCase):
    """P1-1：source.py 的 URL/argv 脱敏恢复。"""

    def test_redact_url_strips_userinfo(self):
        from app.infrastructure.git.source import _redact_url

        self.assertEqual(
            _redact_url("https://user:pass@example.com/group/repo.git"),
            "https://***@example.com/group/repo.git",
        )
        self.assertEqual(
            _redact_url("https://token@example.com/repo"),
            "https://***@example.com/repo",
        )

    def test_redact_url_keeps_plain_url(self):
        from app.infrastructure.git.source import _redact_url

        url = "https://example.com/group/repo.git"
        self.assertEqual(_redact_url(url), url)

    def test_redact_url_plain_text(self):
        from app.infrastructure.git.source import _redact_url

        self.assertEqual(_redact_url("no url here"), "no url here")

    def test_redact_args_masks_url_credentials(self):
        from app.infrastructure.git.source import _redact_args

        self.assertEqual(
            _redact_args(["clone", "https://user:pass@host/repo.git"]),
            ["clone", "https://***@host/repo.git"],
        )

    def test_redact_args_masks_tokenish_argv(self):
        from app.infrastructure.git.source import _redact_args

        token = "Ab3+xF9z" * 5  # 40 位 base64 混排，疑似访问令牌
        self.assertEqual(_redact_args(["header", token]), ["header", "***"])

    def test_redact_args_keeps_git_sha(self):
        from app.infrastructure.git.source import _redact_args

        sha = "a" * 40
        self.assertEqual(_redact_args(["cat-file", "-e", sha]), ["cat-file", "-e", sha])

    def test_redact_args_keeps_normal_args(self):
        from app.infrastructure.git.source import _redact_args

        self.assertEqual(
            _redact_args(["fetch", "--prune", "origin"]),
            ["fetch", "--prune", "origin"],
        )
