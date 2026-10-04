"""What ships: the single-page app and the repository must stay private, offline-capable and honest."""
import json
import re
import shutil
import subprocess
import unittest

from common import ROOT

PAGE = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


class AppPage(unittest.TestCase):
    def test_content_security_policy_blocks_all_network_access(self):
        m = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', PAGE)
        self.assertIsNotNone(m)
        csp = m.group(1)
        for directive in ("default-src 'none'", "connect-src 'none'", "form-action 'none'", "base-uri 'none'"):
            self.assertIn(directive, csp)

    def test_nothing_is_loaded_from_another_site(self):
        # Links to the law are fine (they are plain <a href>). Scripts, styles, fonts and images must be inline.
        self.assertNotRegex(PAGE, r"<script[^>]+src=")
        self.assertNotRegex(PAGE, r"<link[^>]+href=[\"']https?://")
        self.assertNotRegex(PAGE, r"url\(\s*[\"']?https?://")
        self.assertNotIn("fonts.googleapis.com", PAGE)
        self.assertNotIn("fonts.gstatic.com", PAGE)

    def test_no_cookies_storage_or_trackers(self):
        for needle in ("document.cookie", "localStorage", "sessionStorage", "navigator.sendBeacon", "XMLHttpRequest", "fetch("):
            self.assertNotIn(needle, PAGE, needle)

    def test_external_links_do_not_leak_the_page(self):
        for tag in re.findall(r"<a\b[^>]*target=\"_blank\"[^>]*>", PAGE):
            self.assertIn("noopener", tag)

    def test_says_it_is_not_legal_advice_and_that_summaries_are_ai_written(self):
        low = PAGE.lower()
        self.assertIn("not legal advice", low)
        self.assertIn("written by ai", low)


class SpanishView(unittest.TestCase):
    """Static checks on web/app.template.html for the ES / EN toggle (no browser needed). The built page is not read here
    because it only has the Spanish view when outputs/rules_es.json existed at build time."""

    NOTICE_ES = "No es asesoría legal. Solo fuentes públicas, sin revisión de un abogado. Los resúmenes en lenguaje sencillo los escribe una IA."

    @classmethod
    def setUpClass(cls):
        cls.tpl = (ROOT / "web" / "app.template.html").read_text(encoding="utf-8")

    def _dictionary(self):
        block = re.search(r"window\.NAV_ES = \{\n(.*?)\n\};", self.tpl, flags=re.S)
        self.assertIsNotNone(block, "the Spanish dictionary block is missing")
        out = {}
        for line in block.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads("{" + line.rstrip(",") + "}")         # one JSON-style entry per line
            out.update(entry)
        return out

    def test_toggle_language_hooks_exist(self):
        self.assertIn("data-lang", self.tpl)                           # the EN | ES buttons
        self.assertRegex(self.tpl, r"(?<![\w.$])t\(")                  # the translation helper t(...)
        self.assertIn("documentElement.lang", self.tpl)                # the page language follows the choice
        self.assertIn('data-act="lang"', self.tpl)

    def test_spanish_notice_sentence_is_exact(self):
        self.assertIn(self.NOTICE_ES, self.tpl)

    def test_language_is_in_the_url_hash_and_not_in_storage(self):
        self.assertIn('q.set("lang", "es")', self.tpl)                 # written by syncHash
        self.assertIn('q.get("lang") === "es"', self.tpl)              # read by readHash
        for needle in ("document.cookie", "localStorage", "sessionStorage", "navigator.sendBeacon", "XMLHttpRequest", "fetch("):
            self.assertNotIn(needle, self.tpl, needle)

    def test_no_toggle_without_translations(self):
        # The toggle needs translated rules; with none the page stays English only.
        self.assertIn("const HAS_ES = Object.keys(ES_RULES).length > 0;", self.tpl)
        self.assertIn("box.hidden = !HAS_ES;", self.tpl)

    def test_substituted_values_are_escaped(self):
        # t() fills {placeholders} with plain text for text sinks, th() with esc() for markup; te() escapes the whole result.
        self.assertIn("const th = (s, vars) => fill(pick(s), vars, esc);", self.tpl)
        self.assertIn("const te = (s, vars) => esc(t(s, vars));", self.tpl)

    def test_every_translated_source_string_has_a_spanish_entry(self):
        dictionary = self._dictionary()
        body = re.sub(r"window\.NAV_ES = \{.*?\n\};", "", self.tpl, flags=re.S)
        lit = r'"((?:[^"\\]|\\.)*)"'
        keys = [json.loads('"' + m + '"') for m in re.findall(r"(?<![\w.$])t[eh]?\(\s*" + lit, body)]
        for one, many in re.findall(r"(?<![\w.$])tn\(\s*[^,\"]+,\s*" + lit + r"\s*,\s*" + lit, body):
            keys += [json.loads('"' + one + '"'), json.loads('"' + many + '"')]
        self.assertGreater(len(keys), 150)
        missing = sorted({k for k in keys if k not in dictionary})
        self.assertEqual(missing, [])

    def test_spanish_terms_follow_the_brief(self):
        d = self._dictionary()
        for en, es in {
            "Applies": "Se aplica", "Superseded": "Desplazada", "Unknown": "Sin determinar",
            "Not yet effective": "Aún no vigente", "Pending bill": "Proyecto de ley",
            "Rent increase limits": "Límites al aumento de renta", "Just-cause eviction": "Desalojo con causa justificada",
            "Security deposits": "Depósitos de seguridad", "Application and screening fees": "Cuotas de solicitud y evaluación",
            "Tenant screening limits": "Límites a la evaluación de inquilinos", "Algorithmic rent-setting": "Fijación algorítmica de rentas",
            "Which rental laws apply here, on this date?": "¿Qué leyes de alquiler se aplican aquí, en esta fecha?",
        }.items():
            self.assertEqual(d.get(en), es, en)


class Repository(unittest.TestCase):
    TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".js", ".html", ".csv", ".yml", ".yaml", ".example", ".toml", ".cfg"}

    def test_no_api_key_anywhere_in_the_repository(self):
        pattern = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")
        leaks = []
        for p in ROOT.rglob("*"):
            if not p.is_file() or p.name == ".env" or ".git" in p.parts or p.stat().st_size > 5_000_000:
                continue
            if p.suffix in self.TEXT_SUFFIXES or p.name in (".gitignore", ".env.example"):
                if pattern.search(p.read_text(encoding="utf-8", errors="ignore")):
                    leaks.append(str(p.relative_to(ROOT)))
        self.assertEqual(leaks, [])

    def test_env_file_is_git_ignored_and_an_example_exists(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", [l.strip() for l in ignore])
        self.assertTrue((ROOT / ".env.example").exists())

    def test_vercel_config_adds_security_headers(self):
        cfg = (ROOT / "docs" / "vercel.json").read_text(encoding="utf-8")
        for header in ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy"):
            self.assertIn(header, cfg)

    def test_every_documented_dataset_folder_exists(self):
        for rel in ("dataset/README.txt", "README.md", "requirements.txt", "dev/change_tests.json"):
            self.assertTrue((ROOT / rel).exists(), rel)


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class Parity(unittest.TestCase):
    def test_python_and_javascript_engines_agree(self):
        out = subprocess.run(["python3", str(ROOT / "src" / "parity_test.py")], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.returncode, 0, out.stdout[-800:] + out.stderr[-800:])
        self.assertIn("identical", out.stdout)


if __name__ == "__main__":
    unittest.main()
