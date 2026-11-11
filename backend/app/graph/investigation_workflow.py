from app.graph.llm import generate_json_response
from app.models.schemas.investigations import AIInvestigationResult

def run_qa_investigation(context_payload: str) -> AIInvestigationResult:
    """
    Feeds the combined complaint and batch context to the LLM 
    to generate a structured Root Cause Analysis and CAPA using the raw Groq SDK.
    """
    prompt = f"""You are an expert Pharmaceutical Quality Assurance Investigator.
Analyze the provided complaint details alongside the batch production history.
Identify potential root causes, determine the risk level, provide a confidence score (1-100), and recommend Corrective and Preventive Actions (CAPA).

You MUST return the output as a valid JSON object with the following exact keys. Do not include any other text outside the JSON block:
- "root_cause_analysis": (string) Detailed explanation of the potential root cause.
- "capa_recommendation": (string) Actionable steps to correct and prevent recurrence.
- "risk_level": (string) Choose either "High", "Medium", or "Low".
- "confidence_score": (integer) A number between 1 and 100 representing your confidence in this analysis.

CONTEXT TO ANALYZE:
{context_payload}
"""
    
    # Call your existing Groq helper function
    result_dict = generate_json_response(prompt=prompt, temperature=0.2)
    
    # Parse the raw dictionary into the required Pydantic model
    return AIInvestigationResult(**result_dict)