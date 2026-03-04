import os
import httpx
import re
import logging
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PORT = int(os.getenv("PORT", "8080"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("MODEL", "tinyllama:1.1b")

# Guardrails Configuration
MAX_INPUT_LENGTH = 2000

# Prompt injection patterns
INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"ignore\s+all\s+instructions",
    r"disregard\s+previous",
    r"you\s+are\s+now",
    r"new\s+instructions:",
    r"forget\s+(everything|all|your)",
    r"act\s+as\s+if",
    r"override\s+(your|the)\s+(instructions|rules|system)",
    r"repeat\s+(after|the|your)\s+me",
    r"system\s*:\s*",
    r"###\s*instruction",
    r"assistant\s*:\s*(?:ignore|disregard)",
    r"DAN\s+mode",
    r"developer\s+mode",
    r"jailbreak",
    r"sudo\s+mode",
]

# Violence & Weapons patterns
VIOLENCE_PATTERNS = [
    r"\b(bomb|explosive|detonate|c4|dynamite|grenade|ied|pipe\s+bomb)\b",
    r"\b(weapon|gun|firearm|ammunition|rifle|pistol|shotgun)\b",
    r"\b(kill|murder|assassinate|execute)\s+(someone|people|person|him|her)",
    r"\b(hurt|harm|injure|attack|beat)\s+(someone|people|person|him|her|my\s+friend|them)",
    r"how\s+to\s+(kill|murder|assassinate|harm|hurt|injure)",
    r"\b(terrorism|terrorist\s+attack|mass\s+shooting)\b",
    r"\b(torture|mutilate|dismember)\b",
    r"I\s+want\s+to\s+(hurt|harm|kill|murder|beat|attack)\b",
]

# Illegal Drugs patterns (CRITICAL - was missing comprehensive coverage)
DRUG_PATTERNS = [
    r"\b(drug|cocaine|heroin|meth|mdma|lsd|ecstasy|fentanyl)\s+(make|manufacture|produce|cook|synthesize|create)\b",
    r"how\s+(do|can|to)\s+(I|we|you)\s+(make|get|buy|produce|cook|synthesize)\s+.*(cocaine|heroin|meth|drug|mdma|lsd|fentanyl)\b",
    r"\bI\s+want\s+(to\s+)?(do|try|take|use|buy|get|make)\s+(drugs|cocaine|meth|heroin|mdma|lsd|weed|marijuana|fentanyl)\b",
    r"where\s+(can|do)\s+I\s+(buy|get|find)\s+(drugs|cocaine|heroin|meth|mdma|lsd)\b",
    r"\b(sell|deal|traffic)\s+(drugs|cocaine|heroin|meth|mdma|lsd)\b",
    r"\b(snort|inject|smoke|shoot\s+up)\s+(cocaine|heroin|meth|crack)\b",
]

# Self-harm patterns
SELF_HARM_PATTERNS = [
    r"\b(suicide|kill\s+myself|end\s+my\s+life|take\s+my\s+life)\b",
    r"\b(self[- ]?harm|cut\s+myself|hurt\s+myself)\b",
    r"how\s+to\s+(commit\s+)?suicide",
    r"\bI\s+want\s+to\s+(die|kill\s+myself|end\s+it)",
]

# Illegal activity patterns
ILLEGAL_ACTIVITY_PATTERNS = [
    r"\b(hack|exploit|breach|crack)\s+(into|system|network|account|password)\b",
    r"\b(steal|rob|burglary|breaking\s+in|shoplifting)\b",
    r"how\s+to\s+(steal|rob|hack|break\s+into)",
    r"\b(forge|counterfeit|fake)\s+(money|currency|documents|id|passport)\b",
    r"\b(fraud|scam|phishing)\s+(people|users|victims)\b",
]

# Child safety patterns
CHILD_SAFETY_PATTERNS = [
    r"\b(child|minor|underage|kid).{0,50}(sexual|explicit|porn|nude|naked)\b",
    r"\b(pedophile|pedophilia|child\s+abuse)\b",
]

# Category mapping for better error messages
PATTERN_CATEGORIES = {
    'violence': VIOLENCE_PATTERNS,
    'drugs': DRUG_PATTERNS,
    'self_harm': SELF_HARM_PATTERNS,
    'illegal_activity': ILLEGAL_ACTIVITY_PATTERNS,
    'child_safety': CHILD_SAFETY_PATTERNS,
}

# Safe refusal templates
REFUSAL_TEMPLATES = {
    "violence": (
        "I can’t help with requests that involve harming others, weapons, "
        "or violent wrongdoing. If you’re feeling angry or overwhelmed, "
        "I can help you think through safer ways to handle the situation."
    ),

    "drugs": (
        "I can’t assist with obtaining, making, or using illegal drugs. "
        "If you're looking for information about health effects, treatment, "
        "or recovery options, I’d be glad to help."
    ),

    "self_harm": (
        "I’m really sorry that you’re feeling this way. I can’t help with "
        "anything involving self-harm. You don’t have to handle this alone — "
        "if you’re in the India., you can call or text 988 for immediate support. "
        "If you’re elsewhere, I can help find a local resource."
    ),

    "illegal_activity": (
        "I can’t help with planning or carrying out illegal activities. "
        "If you’d like, I can help you explore legal and constructive alternatives."
    ),

    "child_safety": (
        "I can’t engage with content that involves minors in unsafe or exploitative contexts. "
        "If you’re concerned about a child’s safety, please contact local authorities "
        "or an appropriate child protection organization in your area."
    ),

    "privacy": (
        "I can’t help with accessing someone’s private information, accounts, "
        "or data without their consent. If you’re trying to recover your own "
        "account, I can guide you through legitimate recovery options."
    ),

    "hate_or_harassment": (
        "I can’t assist with content that targets or harms individuals or groups. "
        "If you’d like help expressing your concerns in a respectful and constructive way, "
        "I’m here to help."
    ),

    "default": (
        "I can’t help with that request. If you’d like, tell me more about "
        "what you’re trying to accomplish and I’ll do my best to help in a safe way."
    ),
}

# AI Personality - Ultra-simplified for TinyLlama
SYSTEM_PERSONALITY = os.getenv(
    "AI_PERSONALITY",
    "Answer directly and briefly."
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Convert Pydantic validation errors to user-friendly messages"""
    errors = exc.errors()
    if errors:
        # Get the first error message (usually the most relevant)
        first_error = errors[0]
        msg = first_error.get("msg", "Validation error")
        
        # Clean up the message - remove "Value error, " prefix if present
        msg_str = str(msg)
        if msg_str.startswith("Value error, "):
            msg_str = msg_str.replace("Value error, ", "", 1)
        
        return JSONResponse(
            status_code=400,
            content={"error": msg_str}
        )
    
    return JSONResponse(
        status_code=422,
        content={"error": "Invalid input"}
    )

class GenerateIn(BaseModel):
    prompt: str
    temperature: float | None = 0.7
    
    @field_validator('prompt')
    @classmethod
    def validate_prompt(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Prompt cannot be empty")
        if len(v) > MAX_INPUT_LENGTH:
            raise ValueError(f"Prompt too long. Max {MAX_INPUT_LENGTH} characters")
        
        # Check for prompt injection attempts
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, v, re.IGNORECASE):
                logger.warning(f"Prompt injection detected: {v[:100]}...")
                raise ValueError("Prompt injection attempt detected")
        
        # Check for harmful content with category-specific messages
        for category, patterns in PATTERN_CATEGORIES.items():
            for pattern in patterns:
                if re.search(pattern, v, re.IGNORECASE):
                    logger.warning(f"Harmful content detected - Category: {category}, Input: {v[:100]}...")
                    raise ValueError(REFUSAL_TEMPLATES.get(category, REFUSAL_TEMPLATES['default']))
        
        return v.strip()

class AskIn(BaseModel):
    question: str
    temperature: float | None = 0.7
    personality: str | None = None
    
    @field_validator('question')
    @classmethod
    def validate_question(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Question cannot be empty")
        if len(v) > MAX_INPUT_LENGTH:
            raise ValueError(f"Question too long. Max {MAX_INPUT_LENGTH} characters")
        
        # Check for prompt injection attempts
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, v, re.IGNORECASE):
                logger.warning(f"Prompt injection detected: {v[:100]}...")
                raise ValueError("Prompt injection attempt detected")
        
        # Check for harmful content with category-specific messages
        for category, patterns in PATTERN_CATEGORIES.items():
            for pattern in patterns:
                if re.search(pattern, v, re.IGNORECASE):
                    logger.warning(f"Harmful content detected - Category: {category}, Input: {v[:100]}...")
                    raise ValueError(REFUSAL_TEMPLATES.get(category, REFUSAL_TEMPLATES['default']))
        
        return v.strip()

def _is_model_ready():
    """Check if the model is fully loaded in Ollama."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.0)
        if r.status_code == 200:
            models = r.json().get("models", [])
            for m in models:
                if m.get("name") == MODEL or m.get("name", "").startswith(MODEL):
                    return True
    except Exception:
        pass
    return False

@app.get("/health")
def health():
    """Always returns 200 — used by Boltic's liveness probe."""
    return {"ok": True, "model": MODEL, "model_ready": _is_model_ready()}

@app.get("/ready")
def ready():
    """Poll this to know when you can start chatting."""
    return {"ready": _is_model_ready(), "model": MODEL}

def validate_output(text: str) -> tuple[bool, str, str]:
    """Validate model output for harmful content.
    Returns: (is_safe, text_or_refusal, violation_category)
    """
    if not text or not text.strip():
        return True, text, None
    
    # Check output against all harmful patterns
    for category, patterns in PATTERN_CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                logger.error(f"OUTPUT VIOLATION - Category: {category}, Text: {text[:100]}...")
                return False, REFUSAL_TEMPLATES.get(category, REFUSAL_TEMPLATES['default']), category
    
    # Check for common harmful phrases that might slip through
    harmful_phrases = [
        r"(?:drug|cocaine|heroin|meth).*(?:is a )?personal choice",
        r"I can help you (?:make|create|build) (?:a bomb|weapon|explosive)",
        r"here's how to (?:kill|murder|harm)",
        r"you should (?:kill yourself|commit suicide|self[- ]?harm)",
    ]
    
    for phrase in harmful_phrases:
        if re.search(phrase, text, re.IGNORECASE):
            logger.error(f"OUTPUT VIOLATION - Harmful phrase detected: {text[:100]}...")
            return False, REFUSAL_TEMPLATES['default'], 'harmful_phrase'
    
    return True, text, None

@app.post("/generate")
async def generate(payload: GenerateIn):
    logger.info(f"Generate request: {payload.prompt[:50]}...")
    
    body = {
        "model": MODEL,
        "prompt": payload.prompt,
        "stream": False,
        "options": {
            "temperature": payload.temperature,
            "num_predict": 150,
            "stop": ["\n\n", "Question:", "User:", "Asker:"],
        },
    }
    
    # Retry logic for Ollama startup race conditions
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(f"{OLLAMA_URL}/api/generate", json=body)
                r.raise_for_status()
                result = r.json()
                
                # Clean up the response
                response_text = result.get("response", "").strip()
                
                # Remove fake dialogue continuations
                stop_markers = ["\nUser:", "\nQuestion:", "\n\nUser:", "\n\nQuestion:", "\nAI:", "\nAsker:"]
                for marker in stop_markers:
                    if marker in response_text:
                        response_text = response_text.split(marker)[0].strip()
                
                # Remove leading prefixes
                while True:
                    cleaned = response_text
                    for prefix in ["AI:", "Asker:", "Assistant:", "Answer:", "Response:"]:
                        if response_text.lower().startswith(prefix.lower()):
                            response_text = response_text[len(prefix):].strip()
                            break
                    if cleaned == response_text:
                        break
                
                # Validate output before returning
                is_safe, validated_text, violation_category = validate_output(response_text)
                
                if not is_safe:
                    logger.error(f"Blocked unsafe output - Category: {violation_category}")
                    response_text = validated_text
                
                if not response_text:
                    response_text = "I apologize, but I couldn't generate a proper response."
                
                result["response"] = response_text
                return result
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error (attempt {attempt + 1}/{max_retries}): {e.response.status_code} - {e.response.text[:200]}")
            if attempt < max_retries - 1 and e.response.status_code == 500:
                logger.info(f"Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                continue
            raise HTTPException(status_code=503, detail="Model service temporarily unavailable. Please try again.")
        except httpx.RequestError as e:
            logger.error(f"Ollama connection error (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                continue
            raise HTTPException(status_code=503, detail="Cannot connect to model service. Please try again later.")
        except Exception as e:
            logger.error(f"Unexpected error (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            raise HTTPException(status_code=500, detail="An unexpected error occurred. Please try again.")

@app.post("/ask")
async def ask(payload: AskIn):
    logger.info(f"Ask request: {payload.question[:50]}...")
    
    personality = payload.personality or SYSTEM_PERSONALITY
    full_prompt = f"{personality} {payload.question}"
    
    body = {
        "model": MODEL,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": payload.temperature,
            "num_predict": 150,  # Reduced for concise responses
            "stop": ["\n\n", "Question:", "User:", "Asker:"],  # Stop rambling
        },
    }
    
    # Retry logic for Ollama startup race conditions
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(f"{OLLAMA_URL}/api/generate", json=body)
                r.raise_for_status()
                data = r.json()
                answer = data.get("response", "").strip()
                
                # Clean up the response - remove any continuation of fake dialogue
                stop_markers = ["\nUser:", "\nQuestion:", "\n\nUser:", "\n\nQuestion:", "\nAI:", "\nAsker:"]
                for marker in stop_markers:
                    if marker in answer:
                        answer = answer.split(marker)[0].strip()
                
                # Remove leading AI:/Asker:/Assistant: prefixes (case insensitive)
                while True:
                    cleaned = answer
                    for prefix in ["AI:", "Asker:", "Assistant:", "Answer:", "Response:"]:
                        if answer.lower().startswith(prefix.lower()):
                            answer = answer[len(prefix):].strip()
                            break
                    if cleaned == answer:  # No change, we're done
                        break
                
                # CRITICAL: Validate output before returning
                is_safe, validated_answer, violation_category = validate_output(answer)
                
                if not is_safe:
                    logger.error(f"Blocked unsafe output in /ask - Category: {violation_category}")
                    answer = validated_answer
                
                if not answer:
                    answer = "I apologize, but I couldn't generate a proper response."
                
                return {"answer": answer, "model": MODEL}
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error (attempt {attempt + 1}/{max_retries}): {e.response.status_code} - {e.response.text[:200]}")
            if attempt < max_retries - 1 and e.response.status_code == 500:
                logger.info(f"Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                continue
            raise HTTPException(status_code=503, detail="Model service temporarily unavailable. Please try again.")
        except httpx.RequestError as e:
            logger.error(f"Ollama connection error (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                continue
            raise HTTPException(status_code=503, detail="Cannot connect to model service. Please try again later.")
        except Exception as e:
            logger.error(f"Unexpected error (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            raise HTTPException(status_code=500, detail="An unexpected error occurred. Please try again.")

# Serve React frontend static files from dist/
# IMPORTANT: Mount static files at the end so API routes take precedence
_dist = Path(__file__).parent / "dist"
if _dist.exists():
    # Mount static assets directory
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")
    
    # Serve index.html for root path
    @app.get("/", include_in_schema=False)
    async def serve_root():
        """Serve the React app at root"""
        return FileResponse(_dist / "index.html")
    
    # Mount root level static files (logo.png, favicon, etc)
    @app.get("/{filename}", include_in_schema=False)
    async def serve_static_files(filename: str):
        """Serve static files from dist root (logo.png, etc)"""
        file_path = _dist / filename
        # Only serve if it's an actual file (not a directory or non-existent)
        if file_path.is_file() and file_path.name != "index.html":
            return FileResponse(file_path)
        # Otherwise serve index.html for SPA routing
        return FileResponse(_dist / "index.html")

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)