"""
M&A Signal Intelligence Engine - entrypoint.

Two ways to run from the project root:

    python3 run.py
        Interactive: prompts you to type a request (no quotes needed).
        Press Enter with no text to run the built-in example (DEFAULT_TARGET).

    python3 run.py "Find buyers for a distressed German fiber company ~EUR 1.2B"
        One-shot: runs the given free-form request directly (good for scripting).

    Either way, when you give a request a fast lite model extracts the
    structured target from your words, then the full 5-agent pipeline runs.
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

# Single source of truth for the API key lives in orchestrator/.env
load_dotenv(Path(__file__).parent / 'orchestrator' / '.env')

from orchestrator.agent import runner, session_service, APP_NAME

USER_ID = 'analyst'

# Fast, cheap model used only to parse a free-form request into a structured target
EXTRACTION_MODEL = 'gemini-2.5-flash-lite'

# ── DEFAULT ACQUISITION TARGET ──────────────────────────────
# Used when no free-form request is given. Edit to change the built-in example.
# Keys must match what buyer_scoring_tool.score_buyer reads.
DEFAULT_TARGET = {
    'target_name':    '500MW Solar PV Portfolio',
    'target_class':   'renewable_portfolio',  # company | renewable_portfolio | real_estate | infra
    'target_sector':  'renewables',           # one of 18 sectors - see docs/user_guide.md §6
    'target_country': 'Spain',
    'size_eur_m':     450.0,
    'size_mw':        500.0,                   # installed capacity (renewable portfolios only)
}


def default_prompt(target: dict) -> str:
    """Build the kickoff prompt from a structured target."""
    return (
        "Find and rank the most likely buyers for this acquisition target: "
        f"{target['target_name']} "
        f"({target['target_class']}, sector {target['target_sector']}, "
        f"{target['target_country']}, ~EUR {target['size_eur_m']}M)."
    )


def extract_target(request: str) -> dict:
    """Use one fast lite-model call to turn a free-form request into a target_profile."""
    instruction = (
        "Extract the acquisition target from the user's request. Return ONLY JSON:\n"
        '{"target_name": string, '
        '"target_class": "company|renewable_portfolio|real_estate|infra", '
        '"target_sector": "energy|renewables|utilities|technology|telecom|media_entertainment|'
        'financial_services|healthcare|consumer_retail|food_and_beverage|industrials|'
        'chemicals_materials|real_estate|infrastructure|transport_logistics|business_services|'
        'automotive|other", '
        '"target_country": string, "size_eur_m": number, "size_mw": number}\n'
        "size_mw is the installed capacity in MW - applies to renewable portfolios "
        "only (e.g. '500MW'); use null when not applicable or unknown.\n"
        "target_name: if the request does not name a specific company or asset, "
        "compose a short descriptive name from the request itself (e.g. 'Spanish "
        "TMT company with FTTH network') - never null.\n"
        "target_country: infer from nationality adjectives or regions ('Spanish' "
        "-> Spain, 'German' -> Germany); null only if there is no geographic hint.\n"
        "Use null for any other field you cannot determine.\n\n"
        f"Request: {request}"
    )
    # Hold a reference to the client: an inline genai.Client().models.generate_content(...)
    # lets the temporary client be garbage-collected mid-call, closing its connection.
    client = genai.Client()
    resp = client.models.generate_content(
        model=EXTRACTION_MODEL, contents=instruction
    )
    match = re.search(r'\{.*\}', resp.text or '', re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        # Malformed lite-model reply - fall back to an empty profile; main()
        # then names the target from the raw request and warns about the rest.
        return {}


def _strip_md(text: str) -> str:
    """Remove leftover Markdown so agent output prints cleanly in a terminal.

    The strategy agent is instructed to emit plain text; this is a belt-and-
    suspenders backup for the rare stray **bold**, `code`, or # header. Only
    affects what's displayed - the saved JSON trace keeps the original text.
    """
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)                 # **bold** -> bold
    text = re.sub(r'`([^`]*)`', r'\1', text)                     # `code`   -> code
    return re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)  # ## header -> header


async def run_pipeline(target_profile: dict, prompt: str) -> None:
    """Create a session pre-seeded with the target, then stream the pipeline."""
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        state={'target_profile': target_profile},
    )
    message = types.Content(role='user', parts=[types.Part(text=prompt)])

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=message,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            # Join all text parts; skip non-text parts (their .text is None).
            text = '\n'.join(p.text for p in event.content.parts if p.text)
            if text:
                print(f"\n[{event.author}]\n{_strip_md(text)}")


def main() -> None:
    parser = argparse.ArgumentParser(description='M&A Signal Intelligence Engine')
    parser.add_argument(
        'request', nargs='?', default=None,
        help='Free-form acquisition request. Omit to be prompted interactively.',
    )
    args = parser.parse_args()

    # No argument given -> ask interactively (no quotes needed). Blank = example.
    request = args.request
    if request is None:
        request = input(
            "Describe the acquisition target "
            "(or press Enter for the built-in example):\n> "
        ).strip()

    if request:
        target = extract_target(request)   # fast lite-model extraction
        prompt = request                    # use the person's words verbatim
        if not target.get('target_name'):
            target['target_name'] = request[:80]   # never run with a nameless target
        print(f"\nExtracted target_profile: {target}\n")
        missing = [k for k in ('target_country', 'target_sector')
                   if not target.get(k)]
        # Size counts as known if EITHER figure is present - renewable
        # portfolios are size-matched on MW even without a EUR value.
        if not target.get('size_eur_m') and not target.get('size_mw'):
            missing.append('size (EUR or MW)')
        if missing:
            print(
                f"⚠️  Could not determine from your request: {', '.join(missing)}.\n"
                "   Those scoring dimensions will be scored neutrally, so buyer\n"
                "   rankings will be less differentiated. For sharper results,\n"
                "   re-run with the country, sector, and (if known) size included.\n"
            )
    else:
        target = DEFAULT_TARGET
        prompt = default_prompt(DEFAULT_TARGET)
        print("\nNo request given - running the built-in example target.\n")

    try:
        asyncio.run(run_pipeline(target, prompt))
    except genai_errors.ServerError as e:
        # 503 UNAVAILABLE / 500 etc. are transient outages on Google's side,
        # not pipeline bugs. Fail cleanly with guidance instead of a traceback.
        print(
            "\n⚠️  Gemini is temporarily unavailable (transient server error — "
            "typically 503 UNAVAILABLE). This is on Google's side, not the "
            "pipeline. Wait a moment and re-run.\n"
            f"   Detail: {e}"
        )
        sys.exit(1)


if __name__ == '__main__':
    main()
