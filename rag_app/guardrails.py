from llm_guard.input_scanners import PromptInjection, TokenLimit
from llm_guard.output_scanners import Sensitive, NoRefusal
from llm_guard import scan_prompt, scan_output


_input_scanners = [PromptInjection(threshold=0.5), TokenLimit(limit=4096)]
_output_scanners = [Sensitive(), NoRefusal()]


def check_input(prompt: str):
    sanitized, results, scores = scan_prompt(_input_scanners, prompt)
    failed = [name for name, ok in results.items() if not ok]
    return sanitized, failed, scores


def check_output(prompt: str, output: str):
    sanitized, results, scores = scan_output(_output_scanners, prompt, output)
    failed = [name for name, ok in results.items() if not ok]
    return sanitized, failed, scores
