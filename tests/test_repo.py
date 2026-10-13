import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepoTest(unittest.TestCase):
    def test_contest_files_and_placeholder_token(self):
        declaration = (ROOT / "CONTEST_DECLARATION.md").read_text(encoding="utf-8")
        self.assertIn("原创性声明", declaration)
        self.assertIn("信息安全声明", declaration)
        config = (ROOT / "mcp-config.example.json").read_text(encoding="utf-8")
        self.assertIn("${MCD_MCP_TOKEN}", config)
        self.assertNotIn("YOUR_MCP_TOKEN", config)
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or path.suffix not in {".py", ".md", ".json", ".toml"}:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"eyJ[A-Za-z0-9_-]{10,}\.")
            self.assertNotRegex(text, r"Bearer [A-Za-z0-9_-]{20,}")


if __name__ == "__main__":
    unittest.main()
