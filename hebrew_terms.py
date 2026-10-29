"""Tech terms in a Hebrew reply, shown in English on screen; the voice still says them from the Hebrew.

The talker writes English terms in Hebrew letters ("קומיט", "פול ריקווסט"), which it does reliably and the
Hebrew voice says well. Asked to write them in Latin letters it managed 3 times in 9, sometimes slipping into
whole English sentences. So the transcript you read gets them in English here, and the voice is untouched.
A prefix stays Hebrew, joined with a hyphen: "הקומיט" -> "ה-commit". Add a term when you see one left in Hebrew.
"""

import re

TERMS = {  # Hebrew spelling -> English; spelling variants each get a line
    "קומיט": "commit", "קומיטים": "commits",
    "פוש": "push",
    "פול ריקווסט": "pull request", "פול ריקוסט": "pull request", "פול ריקווסטים": "pull requests",
    "פי אר": "PR", "פי.אר": "PR", "פי-אר": "PR",  # "פי.אר." keeps its last dot: it may end the sentence
    "גיטהאב": "GitHub", "גיט האב": "GitHub", "גיט": "Git",
    "בראנץ'": "branch", "ברנץ'": "branch", "בראנצ'ים": "branches", "ברנצ'ים": "branches",
    "מרג'": "merge",
    "דיפלוי": "deploy", "דיפלוימנט": "deployment",
    "טוקן": "token", "טוקנים": "tokens",
    "באג": "bug", "באגים": "bugs",
    "בילד": "build", "בילדים": "builds",
    "טסט": "test", "טסטים": "tests",
    "ריפו": "repo", "ריפוזיטורי": "repository",
    "איי פי איי": "API", "אי פי איי": "API",
    "פרומפט": "prompt", "פרומפטים": "prompts",
    "לוג": "log", "לוגים": "logs",
    "קונפיג": "config",
    "טרמינל": "terminal",
    "ריליס": "release",
    "דוקר": "Docker",
    "פייתון": "Python",
    "ג'ייסון": "JSON",
    "יו אר אל": "URL",
    "קלוד": "Claude", "הייקו": "Haiku", "סונט": "Sonnet",
}
_PREFIXES = "והבכלמש"
_HEBREW = "א-ת"
# Longest first, so "פול ריקווסטים" wins over "פול ריקווסט".
_PATTERN = re.compile(
    rf"(?<![{_HEBREW}'])([{_PREFIXES}]{{0,3}})("
    + "|".join(re.escape(t) for t in sorted(TERMS, key=len, reverse=True))
    + rf")(?![{_HEBREW}'])"
)


def for_screen(text: str) -> str:
    """The talker's Hebrew with its tech terms in English, a prefix joined with a hyphen."""
    def english(m):
        prefix, term = m.group(1), m.group(2)
        if prefix and prefix + term in TERMS:  # the prefix letters are part of a listed word
            return TERMS[prefix + term]
        return (prefix + "-" if prefix else "") + TERMS[term]
    return _PATTERN.sub(english, text)
