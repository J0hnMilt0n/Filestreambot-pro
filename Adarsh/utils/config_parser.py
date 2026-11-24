
# (c) adarsh-goel

from os import environ
from typing import Dict, Optional


class TokenParser:

    def __init__(self, config_file: Optional[str] = None):
        self.tokens = {}
        self.config_file = config_file

    def parse_from_env(self) -> Dict[int, str]:

        self.tokens = {}

        token_variables = sorted(
            (
                (key, value.strip())
                for key, value in environ.items()
                if key.startswith("MULTI_TOKEN") and value.strip()
            ),
            key=lambda item: item[0]
        )

        for index, (_, token) in enumerate(token_variables, start=1):
            self.tokens[index] = token

        return self.tokens
