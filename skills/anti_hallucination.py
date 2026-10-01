"""
Anti-hallucination rules shared across all agents.
Import and append to any agent instruction.
"""


def anti_hallucination(
    missing_data: str = "write N/A and explain why",
    allow_currency_conversion: bool = False,
) -> str:
    """Return anti-hallucination rules for injection into agent instructions.

    allow_currency_conversion:
        False (default) - the agent must preserve the original currency
                          (e.g. news_ingestion_agent, which only reports raw figures).
        True            - the agent may convert, but ONLY via the convert_to_eur tool,
                          never by hand (e.g. signal_extraction / buyer_profiling).
    """
    currency_rule = (
        "- Convert currencies only with the convert_to_eur tool - never estimate a rate by hand"
        if allow_currency_conversion else
        '- Never convert currencies - preserve the original (e.g. "$500M", "EUR 460M")'
    )
    return f"""
        ANTI-HALLUCINATION RULES - NON-NEGOTIABLE:

        SOURCES:
        - Only report information found via google_search or passed from a previous agent
        - Always cite source name and publication date for every claim

        FINANCIAL DATA:
        - Report all figures exactly as written in the source
        {currency_rule}
        - Never invent, estimate, or calculate any figure not explicitly stated

        MISSING DATA:
        - If a data point is not found: {missing_data}
        - Never assume a transaction occurred without an explicit source confirming it

        RATIONALIZATIONS TO REJECT - these do not override the rules above:
        - "The figure is commonly known" - Still need a source. Cite it or omit it.
        - "It's a reasonable estimate" - Estimates are hallucinations. Forbidden.
        - "A complete answer is expected" - Incomplete + cited > complete + guessed.
        - "I'll flag it as approximate" - Approximate financial data is still a hallucination.

        UNKNOWN IS ALWAYS BETTER THAN HALLUCINATED.
    """


# Convenience constant - default behavior: write N/A
ANTI_HALLUCINATION = anti_hallucination()
