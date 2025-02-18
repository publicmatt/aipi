
from typing import Any, Generator
import re
from pydantic import BaseModel

import torch
import ctransformers

from sentence_transformers import SentenceTransformer


# --- Configs --- #

class ModelConfig(BaseModel):
    name: str
    path: str

class TextEmbeddingModelConfig(ModelConfig):
    ...

class TextCompletionModelConfig(ModelConfig):
    ...

# --- Interfaces --- #

class ModelInterface():
    def __init__(self, config: ModelConfig):
        self.config = config
        self.model = None
    def load(self):
        ...
    def __call__(self, input: Any, **kwargs) -> Any:
        ...

class TextEmbeddingModelInterface(ModelInterface):
    def __call__(self, input: str, **kwargs) -> torch.Tensor:
        ...

class TextCompletionModelInterface(ModelInterface):
    def __call__(self, input: str, **kwargs) -> tuple[str, str] | Generator[tuple[str, str | None], Any, Any]:
        ...

# --- Models --- #

# --- Text Embedding

class SentenceTransformerModelConfig(TextEmbeddingModelConfig):
    device: str

class SentenceTransformerModel(TextEmbeddingModelInterface):
    def __init__(self, config: SentenceTransformerModelConfig):
        self.model = None
        self.config = config
    def load(self):
        self.model = SentenceTransformer(self.config.path, device=self.config.device)
    def __call__(self, input: str) -> torch.Tensor:
        return self.model.encode(input, convert_to_numpy=False)

# --- Text Completion

class LlamaCPPModelConfig(TextCompletionModelConfig):
    type: str
    context_length: int
    gpu_layers: int

class LlamaCPPModel(TextCompletionModelInterface):
    def __init__(self, config: LlamaCPPModelConfig):
        print(config)
        self.model = ctransformers.LLM(
                config.path,
                config.type,
                config=ctransformers.Config(
                    context_length=config.context_length,
                    gpu_layers=config.gpu_layers,
                    )
                )
        print(self.model.config)

    def _utf8_is_continuation_byte(self, byte: int) -> bool:
        """Checks if a byte is a UTF-8 continuation byte (most significant bit is 1)."""
        return (byte & 0b10000000) != 0
    
    def _utf8_split_incomplete(self, seq: bytes) -> tuple[bytes, bytes]:
        """Splits a sequence of UTF-8 encoded bytes into complete and incomplete bytes."""
        i = len(seq)
        while i > 0 and self._utf8_is_continuation_byte(seq[i - 1]):
            i -= 1
        return seq[:i], seq[i:]

    def _generate_text(
            self,
            tokens: list[int],
            stops: list[str],
            top_p: float = 0.9,
            temperature: float = 0.8,
            max_tokens: int | None = None,
            repetition_penalty: float = 1.1,
            ) -> tuple[str, str] | Generator[tuple[str, str | None], Any, Any]:

        # Ingest tokens
        self.model.eval(tokens)
    
        stop_regex = re.compile("|".join(map(re.escape, stops)))
    
        count = 0
        finish_reason = None
        text = ''
        incomplete = b''
    
        while True:
            token = self.model.sample(
                    top_p=top_p,
                    temperature=temperature,
                    repetition_penalty=repetition_penalty,
                    )
    
            # finish reason eos
            if self.model.is_eos_token(token):
                finish_reason = 'stop'
                break
    
            # handle incomplete utf-8 multi-byte characters
            incomplete += self.model.detokenize([token], decode=False)
            complete, incomplete = self._utf8_split_incomplete(incomplete)
    
            text += complete.decode('utf-8', errors='ignore')
    
            if stops:
                match = stop_regex.search(text)
                if match:
                    text = text[:match.start()]
                    finish_reason = 'stop'
                    break
    
            # get the length of the longest stop prefix that is at the end of the text
            longest = 0
            for stop in stops:
                for i in range(len(stop), 0, -1):
                    if text.endswith(stop[:i]):
                        longest = max(longest, i)
                        break
    
            # text[:end] is the text without the stop
            end = len(text) - longest
            if end > 0:
                yield text[:end], finish_reason
                # save the rest of the text incase the stop prefix doesn't generate a full stop
                text = text[end:]
    
            count += 1
            if max_tokens and count >= max_tokens:
                finish_reason = 'length'
                break
    
            self.model.eval([token])
        yield text, finish_reason
        self.model.reset()

    def __call__(self, input: str, **kwargs) -> tuple[str, str] | Generator[tuple[str, str | None], Any, Any]:
        return self._generate_text(self.model.tokenize(input), **kwargs)


