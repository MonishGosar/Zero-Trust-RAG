import re


class FakeAzure:
    """Deterministic test double. Never used by the production application."""

    def __init__(self):
        self.answer_text = "APAC revenue was $82M. [1]"
        self.last_sources = []
        self.fail_embeddings = False
        self.answer_calls = 0

    def embed(self, texts):
        if self.fail_embeddings:
            raise RuntimeError("test secret must not reach response")
        keywords = ["apac", "revenue", "leave", "holiday", "security", "europe"]
        return [
            [float(len(re.findall(word, text.lower()))) for word in keywords] + [0.1]
            for text in texts
        ]

    def answer(self, question, sources):
        self.answer_calls += 1
        self.last_sources = sources
        return self.answer_text

    def close(self):
        pass
