"""
Pipeline cost gate.

`skip_if_no_ranked_buyers` builds a ``before_agent_callback`` that skips an
agent (returning a canned response instead of running the model) when deal
matching ran but produced zero buyers - e.g. buyer profiling died mid-run with
an empty or malformed output. A strategy call over an empty ranked list costs
a model call and yields boilerplate, so it is short-circuited instead.
Returning Content from a before_agent_callback short-circuits the agent in ADK.
"""
from google.genai import types


def skip_if_no_ranked_buyers(skip_response: str):
    """Skip the agent when deal matching ran but produced zero buyers.

    Inert when deal_matching_results is absent or has any ranked/excluded buyer.
    """
    def _callback(callback_context):
        results = callback_context.state.get('deal_matching_results')
        if results is not None and not results.get('ranked') and not results.get('excluded'):
            return types.Content(role='model', parts=[types.Part(text=skip_response)])
        return None
    return _callback
