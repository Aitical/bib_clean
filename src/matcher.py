import sqlite3
import os
import re
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from openai import OpenAI
from src import db

@dataclass
class MatchResult:
    key: Optional[str]
    source: str  # 'exact', 'llm', 'none', 'error'
    confidence: float

class FuzzyMatcher:
    def __init__(self, conn: sqlite3.Connection, api_key: str = None, base_url: str = None, model: str = "gpt-3.5-turbo"):
        self.conn = conn
        self.canonical = db.get_canonical_strings(conn)
        self.model = model

        # Setup OpenAI client
        # Priority: explicit arg -> env var
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")

        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            self.client = None

        # Build reverse lookup map for Exact Match
        # Maps lowercase full names/keys to Canonical Key
        self.exact_map = {}
        for k, v in self.canonical.items():
            self.exact_map[k.lower()] = k
            self.exact_map[v.lower()] = k

    def match(self, raw_string: str) -> MatchResult:
        if not raw_string:
            return MatchResult(None, 'none', 0.0)

        raw_lower = raw_string.strip().lower()
        # Remove braces if present? (Bibtex sometimes has {Journal})
        # parser.py might handle it, but raw_string here comes from DB json.
        raw_lower = raw_lower.strip('{}')

        # 1. Exact Match
        if raw_lower in self.exact_map:
            return MatchResult(self.exact_map[raw_lower], 'exact', 1.0)

        # 2. LLM Match
        if self.client:
            return self._llm_match(raw_string)

        return MatchResult(None, 'none', 0.0)

    def _llm_match(self, raw_string: str) -> MatchResult:
        # Pre-filter candidates to avoid huge prompts
        # Strategy: word overlap + Jaccard similarity or simple inclusion
        # Since we deal with abbreviations, we must be careful.

        raw_tokens = set(re.findall(r'\w+', raw_string.lower()))

        scored_candidates = []
        for k, v in self.canonical.items():
            cand_tokens = set(re.findall(r'\w+', k.lower())) | set(re.findall(r'\w+', v.lower()))
            intersection = raw_tokens & cand_tokens
            score = len(intersection)
            scored_candidates.append((score, k, v))

        # Sort by score descending
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        # Take top 30
        top_candidates = [(k, v) for s, k, v in scored_candidates[:30]]

        candidates_str = "\n".join([f"- Key: {k}, Full: {v}" for k, v in top_candidates])

        prompt = f"""
        You are a BibTeX normalization expert.
        Your task is to match the given raw journal/conference name to one of the canonical keys.

        Raw String: "{raw_string}"

        Candidates:
        {candidates_str}

        Instructions:
        1. Identify if the raw string refers to one of the candidates.
        2. It might be an abbreviation, a full name, or a variation.
        3. If it matches, return the Key.
        4. If it does not match any candidate clearly, return "NONE".

        Output format: Just the Key (or NONE). Do not output explanation.
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )
            content = response.choices[0].message.content.strip()

            # Clean output (remove quotes, dots)
            content = content.strip('"').strip("'").strip('.')

            if content == "NONE":
                 return MatchResult(None, 'llm_miss', 0.0)

            # Check if valid key
            if content in self.canonical:
                return MatchResult(content, 'llm', 0.9)
            else:
                return MatchResult(None, 'llm_hallucination', 0.0)

        except Exception as e:
            print(f"LLM Error: {e}")
            return MatchResult(None, 'error', 0.0)
