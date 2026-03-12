import os
import httpx
import re
import logging
import asyncio
import time
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
MODEL = os.getenv("MODEL", "qwen2.5:1.5b")
# Comma-separated list of allowed CORS origins (set in .env for production)
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:4173").split(",")
    if o.strip()
]

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

# ── S1: Violent Crimes ──────────────────────────────────────────────────────
S1_VIOLENT_CRIMES = [
    r"\b(kill|murder|assassinate|execute)\s+(someone|people|person|him|her|them)\b",
    r"\b(hurt|harm|injure|attack|beat|stab|shoot)\s+(someone|people|person|him|her|my\s+\w+|them)\b",
    r"how\s+to\s+(kill|murder|assassinate|harm|hurt|injure|attack|stab|shoot)\b",
    r"\b(terrorism|terrorist\s+attack|mass\s+shooting|mass\s+murder|genocide)\b",
    r"\b(torture|mutilate|dismember|maim)\b",
    r"I\s+want\s+to\s+(hurt|harm|kill|murder|beat|attack|stab|shoot)\b",
    r"\b(assault|kidnap|abduct)\s+(someone|a\s+person|him|her|them)\b",
    r"how\s+to\s+(commit|carry\s+out)\s+(a\s+)?(murder|assault|attack|kidnapping)\b",
    r"\b(serial\s+killer|hitman|contract\s+kill)\b",
    r"plan\s+(to\s+)?(kill|murder|attack|harm)\s+(someone|a\s+person|people)\b",
    # ── Arson / fire attacks ──
    r"\b(arson|arsonist|firebomb|fire.?bomb|molotov)\b",
    r"how\s+to\s+(blast|blow\s+up|destroy|burn\s+down|torch|set\s+(fire|alight)\s+to)\s+(a\s+)?(car|vehicle|building|house|property|bus|truck)\b",
    r"\b(blast|blow\s+up)\s+(a\s+)?(car|vehicle|building|house|property)\b",
    r"using\s+(petrol|gasoline|fuel|accelerant).{0,40}(burn|blast|destroy|set\s+fire|torch|ignite)\b",
    r"(pour|splash|douse).{0,30}(petrol|gasoline|fuel|accelerant).{0,30}(car|vehicle|building|house)\b",
    r"how\s+to\s+(start\s+a\s+fire|ignite).{0,30}(car|vehicle|building|house)\b",
]

# ── S2: Non-Violent Crimes ────────────────────────────────────────────────────
S2_NONVIOLENT_CRIMES = [
    r"\b(hack|exploit|breach|crack)\s+(into|a\s+)?(system|network|account|password|server|database)\b",
    r"how\s+to\s+(steal|rob|shoplift|burglarize|break\s+into|pick\s+a\s+lock)\b",
    r"\b(forge|counterfeit|fake)\s+(money|currency|documents|id|passport|signature|check)\b",
    r"\b(fraud|scam|phishing|identity\s+theft|ponzi|embezzle)\b",
    r"how\s+to\s+(launder|hide)\s+money\b",
    r"\b(ransomware|malware|trojan|keylogger|spyware)\s+(create|build|make|deploy|install)\b",
    r"how\s+to\s+(evade|avoid)\s+(taxes|tax|the\s+irs|customs)\b",
    r"how\s+to\s+(hotwire|steal)\s+(a\s+)?(car|vehicle)\b",
    r"\b(bribe|blackmail|extort)\s+(someone|a\s+person|official|police)\b",
    r"how\s+to\s+make\s+(fake|counterfeit)\s+(money|currency|bills?|id|documents?)\b",
]

# ── S3: Sex-Related Crimes ────────────────────────────────────────────────────
S3_SEX_CRIMES = [
    r"\b(rape|sexual\s+assault|molest)\s+(someone|a\s+person|him|her|them)\b",
    r"how\s+to\s+(drug|spike)\s+(someone.{0,20}drink|a\s+drink)\b",
    r"how\s+to\s+(get\s+away\s+with|commit)\s+(rape|sexual\s+assault)\b",
    r"\b(sex\s+trafficking|human\s+trafficking)\b",
    r"how\s+to\s+(coerce|force|pressure)\s+someone\s+(into\s+sex|to\s+have\s+sex)\b",
    r"\b(non.?consensual|without\s+consent).{0,40}(sex|intercourse|touching)\b",
    r"how\s+to\s+(film|record)\s+someone\s+(without|secretly).{0,30}(naked|sex|nude)\b",
]

# ── S4: Child Sexual Exploitation ────────────────────────────────────────────
S4_CHILD_SAFETY = [
    r"\b(child|minor|underage|kid|teen).{0,50}(sexual|explicit|porn|nude|naked|erotic)\b",
    r"\b(pedophile|pedophilia|child\s+sexual\s+abuse|csam|csem)\b",
    r"\b(groom|grooming)\s+(a\s+)?(child|minor|kid|teen)\b",
    r"how\s+to\s+(attract|seduce|approach)\s+(children|minors|kids|a\s+child|a\s+minor)\b",
    r"sexual.{0,30}(child|minor|kid|underage|teen)\b",
]

# ── S5: Defamation ────────────────────────────────────────────────────────────
S5_DEFAMATION = [
    r"write\s+(a\s+)?(fake|false|fabricated|made.up)\s+(story|article|news|post|review).{0,40}(about|against)\b",
    r"make\s+(someone|a\s+person|him|her)\s+look\s+(guilty|criminal|bad|evil)\s+(with\s+)?(false|fake|lies)\b",
    r"spread\s+(false|fake|fabricated)\s+(rumor|information|news|story)\s+(about|against)\b",
    r"how\s+to\s+(defame|slander|libel)\s+(someone|a\s+person)\b",
    r"(create|write|generate)\s+(fake|false)\s+(accusation|allegation).{0,30}(against|about)\b",
]

# ── S6: Specialized Advice (dangerous substances & illegal drugs) ─────────────
S6_SPECIALIZED_ADVICE = [
    r"how\s+to\s+(make|build|create|synthesize|produce)\s+(poison|toxin|venom|ricin|sarin|vx\s+gas|nerve\s+agent)\b",
    r"\b(poison|toxic|lethal)\s+(dose|amount|quantity).{0,30}(kill|harm|hurt|injure|murder)\b",
    r"how\s+to\s+(make|create|brew|distill)\s+(methamphetamine|meth|crystal\s+meth|heroin|fentanyl|crack)\b",
    r"\b(drug|cocaine|coke|heroin|meth|mdma|lsd|ecstasy|fentanyl)\s+(make|manufacture|produce|cook|synthesize|create)\b",
    r"how\s+(do|can|to)\s+(I|we|you)\s+(make|get|buy|produce|cook|synthesize|consume|use|take)\s+.*(cocaine|coke|heroin|meth|drug|mdma|lsd|fentanyl)\b",
    r"how\s+to\s+(consume|use|take|smoke|snort|inject|ingest|do|try|buy|get|make|cook|produce)\s+.*(cocaine|coke|heroin|meth|drugs|mdma|lsd|fentanyl|crack|ecstasy)\b",
    r"\bI\s+want\s+(to\s+)?(do|try|take|use|buy|get|make|consume)\s+(drugs|cocaine|coke|meth|heroin|mdma|lsd|fentanyl)\b",
    r"where\s+(can|do)\s+I\s+(buy|get|find)\s+(drugs|cocaine|coke|heroin|meth|mdma|lsd)\b",
    r"\b(sell|deal|traffic)\s+(drugs|cocaine|coke|heroin|meth|mdma|lsd)\b",
    r"\b(snort|inject|shoot\s+up)\s+(cocaine|coke|heroin|meth|crack)\b",
]

# ── S7: Privacy ───────────────────────────────────────────────────────────────
S7_PRIVACY = [
    r"how\s+to\s+(stalk|track|spy\s+on|surveil|monitor)\s+(someone|a\s+person|him|her)\s+(without|secretly)\b",
    r"how\s+to\s+(find|get|access)\s+(someone.{0,20})?(address|location|phone\s+number|ssn|social\s+security)\s+without\b",
    r"\b(dox|doxx|doxing|doxxing)\s+(someone|a\s+person|him|her)\b",
    r"how\s+to\s+(hack|access|break\s+into)\s+(someone.{0,20})?(email|account|phone|device|computer)\b",
    r"how\s+to\s+(install|put)\s+(spyware|stalkerware|keylogger|tracking\s+software)\s+on\s+(someone|their)\b",
    r"\b(non.?consensual|without\s+permission|without\s+consent).{0,40}(photo|image|video|record|film|share)\b",
]

# ── S8: Intellectual Property ─────────────────────────────────────────────────
S8_IP = [
    r"how\s+to\s+(pirate|illegally\s+download|torrent)\s+(movies?|music|software|games?|books?)\b",
    r"how\s+to\s+(bypass|crack|remove|circumvent)\s+(drm|copyright\s+protection|license)\b",
    r"(copy|reproduce|steal|plagiarize).{0,30}(copyrighted|patented|trademarked)\s+(work|content|material|code|design)\b",
    r"how\s+to\s+(sell|distribute)\s+(pirated|cracked|stolen)\s+(software|content|movies?|music)\b",
]

# ── S9: Indiscriminate Weapons (CBRN + illegal firearms) ──────────────────────
S9_WEAPONS = [
    r"\b(bomb|explosive|detonate|c4|dynamite|grenade|ied|pipe\s+bomb|landmine|claymore)\b",
    r"how\s+to\s+(make|build|create|assemble|construct)\s+(a\s+)?(bomb|explosive|grenade|ied|pipe\s+bomb|landmine)\b",
    r"\b(nuclear|biological|chemical|radiological)\s+(weapon|bomb|attack|agent|warfare)\b",
    r"how\s+to\s+(enrich|obtain|acquire)\s+(uranium|plutonium|radioactive)\b",
    r"\b(anthrax|smallpox|plague|ebola|bioweapon|bio.?weapon)\b",
    r"how\s+to\s+(synthesize|produce|make|weaponize)\s+(sarin|vx|mustard\s+gas|nerve\s+agent|chlorine\s+gas)\b",
    r"\b(fully\s+automatic|convert.{0,20}semi.{0,10}auto|illegal\s+modification)\s+(gun|rifle|weapon|firearm)\b",
    r"how\s+to\s+(3d\s+print|manufacture|make)\s+(a\s+)?(gun|firearm|weapon|suppressor|silencer)\b",
    r"how\s+to\s+(obtain|buy|acquire)\s+(guns?|weapons?|firearms?|ammo)\s+(illegally|without\s+(a\s+)?background\s+check)\b",
]

# ── S10: Hate ─────────────────────────────────────────────────────────────────
S10_HATE = [
    r"\b(kill|exterminate|eliminate|wipe\s+out)\s+(all\s+)?(jews?|muslims?|christians?|black\s+people|white\s+people|gay|lgbtq|immigrants?)\b",
    r"\b(white\s+supremacy|white\s+power|nazi|neo.?nazi|kkk|ku\s+klux\s+klan)\b",
    r"\b(n[i1]gg[e3]r|ch[i1]nk|sp[i1]c|k[i1]ke|f[a4]gg[o0]t)\b",
    r"\b(jews?|muslims?|blacks?|gays?|immigrants?|refugees?)\s+.{0,30}\b(inferior|subhuman|evil|filthy|vermin|animals?|parasites?)\b",
    r"(write|create|generate|make).{0,30}(racist|antisemitic|islamophobic|homophobic|transphobic|sexist)\s+(joke|content|speech|post|story|propaganda)\b",
    r"\b(racial|ethnic|religious)\s+(cleansing|purge|genocide)\b",
    r"\b(supremacist|extremist|radicalize)\s+(group|ideology|content|propaganda)\b",
]

# ── S11: Suicide & Self-Harm ──────────────────────────────────────────────────
S11_SELF_HARM = [
    r"\b(suicide|kill\s+myself|end\s+my\s+life|take\s+my\s+life|end\s+it\s+all)\b",
    r"\b(self[- ]?harm|cut\s+myself|hurt\s+myself|burn\s+myself)\b",
    r"how\s+to\s+(commit\s+)?suicide",
    r"\bI\s+want\s+to\s+(die|kill\s+myself|end\s+it|disappear\s+forever)\b",
    r"how\s+to\s+(overdose|od)\s+(on|from)\b",
    r"what\s+(pills?|drugs?|medication).{0,30}(kill|overdose|lethal|suicide)\b",
    r"how\s+(many|much).{0,20}(pills?|tablets?|mg).{0,20}(kill|lethal|fatal|overdose)\b",
    r"\b(methods?|ways?)\s+(to\s+)?(commit\s+)?suicide\b",
    r"I\s+(am\s+going\s+to|will|plan\s+to)\s+(kill\s+myself|end\s+my\s+life|commit\s+suicide)\b",
]

# ── S12: Sexual Content ───────────────────────────────────────────────────────
S12_SEXUAL = [
    r"write\s+(a\s+)?(explicit|graphic|erotic|sexual|nsfw|pornographic)\s+(story|scene|content|description|fantasy)\b",
    r"\b(porn|pornography|pornographic|explicit\s+sex|graphic\s+sex)\s+(create|write|generate|make|produce)\b",
    r"describe\s+(in\s+detail\s+)?(sexual|explicit|erotic|graphic).{0,30}(act|scene|encounter)\b",
    r"\b(sexual|erotic|explicit)\s+(roleplay|rp)\b",
    r"(generate|create|write|make).{0,20}(nude|naked|topless|explicit)\s+(image|photo|picture|description)\b",
]

# Category mapping — all 12 hazard categories
PATTERN_CATEGORIES = {
    'S1_violent_crimes':     S1_VIOLENT_CRIMES,
    'S2_nonviolent_crimes':  S2_NONVIOLENT_CRIMES,
    'S3_sex_crimes':         S3_SEX_CRIMES,
    'S4_child_safety':       S4_CHILD_SAFETY,
    'S5_defamation':         S5_DEFAMATION,
    'S6_specialized_advice': S6_SPECIALIZED_ADVICE,
    'S7_privacy':            S7_PRIVACY,
    'S8_ip':                 S8_IP,
    'S9_weapons':            S9_WEAPONS,
    'S10_hate':              S10_HATE,
    'S11_self_harm':         S11_SELF_HARM,
    'S12_sexual':            S12_SEXUAL,
}

# Safe refusal templates
REFUSAL_TEMPLATES = {
    "S1_violent_crimes": (
        "I can't help with requests that involve violent crimes or harming others. "
        "If you're feeling angry or overwhelmed, I can help you think through safer ways to handle the situation."
    ),
    "S2_nonviolent_crimes": (
        "I can't help with planning or carrying out illegal activities. "
        "If you'd like, I can help you explore legal and constructive alternatives."
    ),
    "S3_sex_crimes": (
        "I can't assist with content related to sexual crimes or non-consensual acts. "
        "If you need support, please reach out to a trusted authority or helpline."
    ),
    "S4_child_safety": (
        "I can't engage with content that involves minors in unsafe or exploitative contexts. "
        "If you're concerned about a child's safety, please contact local authorities "
        "or an appropriate child protection organization in your area."
    ),
    "S5_defamation": (
        "I can't help create false or defamatory content about individuals or groups. "
        "If you'd like help with factual, constructive writing, I'm happy to assist."
    ),
    "S6_specialized_advice": (
        "I can't assist with obtaining, making, or using illegal drugs or dangerous substances. "
        "If you're looking for information about health effects, treatment, "
        "or recovery options, I'd be glad to help."
    ),
    "S7_privacy": (
        "I can't help with accessing someone's private information, tracking, or surveilling "
        "others without their consent. If you're trying to recover your own account, "
        "I can guide you through legitimate recovery options."
    ),
    "S8_ip": (
        "I can't help with pirating, plagiarizing, or circumventing intellectual property protections. "
        "I can help you find legal ways to access or use the content you're looking for."
    ),
    "S9_weapons": (
        "I can't help with requests involving weapons, explosives, or CBRN materials. "
        "If you're feeling unsafe or threatened, please contact local emergency services."
    ),
    "S10_hate": (
        "I can't assist with content that promotes hate, discrimination, or violence against "
        "any individual or group. If you'd like help expressing concerns constructively, I'm here."
    ),
    "S11_self_harm": (
        "I'm really sorry that you're feeling this way. I can't help with anything involving "
        "self-harm or suicide. You don't have to handle this alone — "
        "please reach out to iCall at 9152987821 (India) or a local crisis helpline. "
        "I can help you find support resources."
    ),
    "S12_sexual": (
        "I can't assist with generating explicit or pornographic content. "
        "If you have questions about health, relationships, or wellness, I'm happy to help."
    ),
    "default": (
        "I can't help with that request. If you'd like, tell me more about "
        "what you're trying to accomplish and I'll do my best to help in a safe way."
    ),
}

# ── Shared input validator ────────────────────────────────────────────────────
def validate_input(text: str) -> str:
    """Single source-of-truth validator for all user-supplied text fields."""
    if not text or not text.strip():
        raise ValueError("Input cannot be empty")
    if len(text) > MAX_INPUT_LENGTH:
        raise ValueError(f"Input too long. Max {MAX_INPUT_LENGTH} characters")
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            logger.warning(f"Prompt injection detected: {text[:100]}...")
            raise ValueError("Prompt injection attempt detected")
    for category, patterns in PATTERN_CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning(f"Harmful content detected - Category: {category}, Input: {text[:100]}...")
                raise ValueError(REFUSAL_TEMPLATES.get(category, REFUSAL_TEMPLATES['default']))
    return text.strip()

# ── Shared LLM output cleaner ─────────────────────────────────────────────────
_STOP_MARKERS = ["\nUser:", "\nQuestion:", "\n\nUser:", "\n\nQuestion:", "\nAI:", "\nAsker:"]
_LEADING_PREFIXES = ["AI:", "Asker:", "Assistant:", "Answer:", "Response:"]

def clean_llm_text(text: str) -> str:
    """Strip fake-dialogue continuations and leading role-prefixes from LLM output."""
    for marker in _STOP_MARKERS:
        if marker in text:
            text = text.split(marker)[0].strip()
    while True:
        snapshot = text
        for prefix in _LEADING_PREFIXES:
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
                break
        if snapshot == text:
            break
    return text

# AI Personality - Optimized for Phi-3 Mini
SYSTEM_PERSONALITY = os.getenv(
    "AI_PERSONALITY",
    "Answer directly with specific facts, names, and examples. Do not repeat the question."
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
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
        return validate_input(v)

class AskIn(BaseModel):
    question: str
    temperature: float | None = 0.7
    personality: str | None = None
    
    @field_validator('question')
    @classmethod
    def validate_question(cls, v: str) -> str:
        return validate_input(v)

_model_ready_cache: dict = {"result": False, "expires": 0.0}

def _is_model_ready() -> bool:
    """Check if the model is fully loaded in Ollama (result cached for 5 s)."""
    now = time.monotonic()
    if now < _model_ready_cache["expires"]:
        return _model_ready_cache["result"]
    result = False
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.0)
        if r.status_code == 200:
            models = r.json().get("models", [])
            for m in models:
                if m.get("name") == MODEL or m.get("name", "").startswith(MODEL):
                    result = True
                    break
    except Exception:
        pass
    _model_ready_cache["result"] = result
    _model_ready_cache["expires"] = now + 5.0
    return result

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
        r"here'?s?\s+how\s+to\s+(?:kill|murder|harm|blast|blow\s+up|burn|destroy|torch)",
        r"you should (?:kill yourself|commit suicide|self[- ]?harm)",
        # Catch numbered/step-by-step destructive instructions
        r"step\s*\d+\.?.{0,80}(?:petrol|gasoline|fuel|accelerant|ignite|detonate|explode)",
        r"(?:pour|splash|douse|add).{0,30}(?:petrol|gasoline|fuel).{0,30}(?:tank|car|engine)",
        r"(?:blast|blow\s+up|torch|set\s+fire|burn\s+down).{0,30}(?:car|vehicle|building|house)",
    ]
    
    for phrase in harmful_phrases:
        if re.search(phrase, text, re.IGNORECASE):
            logger.error(f"OUTPUT VIOLATION - Harmful phrase detected: {text[:100]}...")
            return False, REFUSAL_TEMPLATES['default'], 'harmful_phrase'
    
    return True, text, None

@app.post("/generate")
async def generate(payload: GenerateIn):
    logger.info(f"Generate request: {payload.prompt[:50]}...")

    if not _is_model_ready():
        raise HTTPException(
            status_code=503,
            detail=f"Model '{MODEL}' is still loading, please wait a moment and try again."
        )

    body = {
        "model": MODEL,
        "prompt": payload.prompt,
        "stream": False,
        "options": {
            "temperature": payload.temperature,
            "num_predict": 500,
            "num_ctx": 2048,
            "stop": ["Question:", "User:", "Asker:"],
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
                response_text = clean_llm_text(result.get("response", "").strip())
                
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

    if not _is_model_ready():
        raise HTTPException(
            status_code=503,
            detail=f"Model '{MODEL}' is still loading, please wait a moment and try again."
        )

    personality = payload.personality or SYSTEM_PERSONALITY
    # Cap personality length to avoid issues with long system prompts
    if len(personality) > 200:
        personality = personality[:200].rsplit('.', 1)[0] + '.'
    
    body = {
        "model": MODEL,
        "system": personality,
        "prompt": payload.question,
        "stream": False,
        "options": {
            "temperature": payload.temperature,
            "num_predict": 800,
            "num_ctx": 2048,
            "stop": ["Question:", "User:", "Asker:"],
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
                answer = clean_llm_text(data.get("response", "").strip())
                
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