import os
import uuid
import httpx
import re
import logging
import asyncio
import time
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import uvicorn

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
PORT               = int(os.getenv("PORT", "8080"))
OLLAMA_URL         = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL              = os.getenv("MODEL", "gemma3:4b")
OLLAMA_NUM_PARALLEL = int(os.getenv("OLLAMA_NUM_PARALLEL", "4"))
NUM_THREADS        = int(os.getenv("OLLAMA_NUM_THREADS", "4"))
RATE_LIMIT         = os.getenv("RATE_LIMIT", "120/minute")
QUEUE_TIMEOUT_SECONDS = float(os.getenv("QUEUE_TIMEOUT_SECONDS", "45"))
MAX_QUEUE_DEPTH    = int(os.getenv("MAX_QUEUE_DEPTH", "16"))
DEFAULT_TEMPERATURE = float(os.getenv("DEFAULT_TEMPERATURE", "0.45"))
DEFAULT_NUM_PREDICT = int(os.getenv("DEFAULT_NUM_PREDICT", "2048"))
DEFAULT_NUM_CTX    = int(os.getenv("DEFAULT_NUM_CTX", "16384"))
DEFAULT_NUM_BATCH  = int(os.getenv("DEFAULT_NUM_BATCH", "512"))
MODEL_READY_TTL_SECONDS = float(os.getenv("MODEL_READY_TTL_SECONDS", "5"))
USE_MMAP          = os.getenv("OLLAMA_USE_MMAP", "1").strip().lower() not in {"0", "false", "no", "off"}
KEEP_ALIVE        = os.getenv("OLLAMA_KEEP_ALIVE", "-1")

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:5174,http://localhost:4173").split(",")
    if o.strip()
]

MAX_INPUT_LENGTH  = int(os.getenv("MAX_INPUT_LENGTH", "2000"))
# Keep recent turns within the reduced-context serverless profile.
MAX_HISTORY_CHARS = int(os.getenv("MAX_HISTORY_CHARS", "40000"))

# ── Prompt injection patterns ─────────────────────────────────────────────────
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

# ── S1: Violent Crimes ────────────────────────────────────────────────────────
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

# ── S4: Child Sexual Exploitation ─────────────────────────────────────────────
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

# ── S6: Specialized Advice ────────────────────────────────────────────────────
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

# ── S9: Indiscriminate Weapons ────────────────────────────────────────────────
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

COMPILED_INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE) for pattern in INJECTION_PATTERNS
]
COMPILED_PATTERN_CATEGORIES = {
    category: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for category, patterns in PATTERN_CATEGORIES.items()
}

# ── Input validator ───────────────────────────────────────────────────────────
def validate_input(text: str) -> str:
    if not text or not text.strip():
        raise ValueError("Input cannot be empty")
    if len(text) > MAX_INPUT_LENGTH:
        raise ValueError(f"Input too long. Max {MAX_INPUT_LENGTH} characters")
    for pattern in COMPILED_INJECTION_PATTERNS:
        if pattern.search(text):
            logger.warning(f"Prompt injection detected: {text[:100]}...")
            raise ValueError("Prompt injection attempt detected")
    for category, patterns in COMPILED_PATTERN_CATEGORIES.items():
        for pattern in patterns:
            if pattern.search(text):
                logger.warning(f"Harmful content [{category}]: {text[:100]}...")
                raise ValueError(REFUSAL_TEMPLATES.get(category, REFUSAL_TEMPLATES['default']))
    return text.strip()

# ── LLM output cleaner ────────────────────────────────────────────────────────
_STOP_MARKERS     = ["\nUser:", "\nQuestion:", "\n\nUser:", "\n\nQuestion:", "\nAI:", "\nAsker:"]
_LEADING_PREFIXES = ["AI:", "Asker:", "Assistant:", "Answer:", "Response:"]

def clean_llm_text(text: str) -> str:
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

# ── Output validator ──────────────────────────────────────────────────────────
_HARMFUL_PHRASES = [
    r"(?:drug|cocaine|heroin|meth).*(?:is a )?personal choice",
    r"I can help you (?:make|create|build) (?:a bomb|weapon|explosive)",
    r"here'?s?\s+how\s+to\s+(?:kill|murder|harm|blast|blow\s+up|burn|destroy|torch)",
    r"you should (?:kill yourself|commit suicide|self[- ]?harm)",
    r"step\s*\d+\.?.{0,80}(?:petrol|gasoline|fuel|accelerant|ignite|detonate|explode)",
    r"(?:pour|splash|douse|add).{0,30}(?:petrol|gasoline|fuel).{0,30}(?:tank|car|engine)",
    r"(?:blast|blow\s+up|torch|set\s+fire|burn\s+down).{0,30}(?:car|vehicle|building|house)",
]

COMPILED_HARMFUL_PHRASES = [
    re.compile(pattern, re.IGNORECASE) for pattern in _HARMFUL_PHRASES
]

def validate_output(text: str) -> tuple[bool, str, str | None]:
    if not text or not text.strip():
        return True, text, None
    for category, patterns in COMPILED_PATTERN_CATEGORIES.items():
        for pattern in patterns:
            if pattern.search(text):
                logger.error(f"OUTPUT VIOLATION [{category}]: {text[:100]}...")
                return False, REFUSAL_TEMPLATES.get(category, REFUSAL_TEMPLATES['default']), category
    for phrase in COMPILED_HARMFUL_PHRASES:
        if phrase.search(text):
            logger.error(f"OUTPUT VIOLATION [harmful_phrase]: {text[:100]}...")
            return False, REFUSAL_TEMPLATES['default'], 'harmful_phrase'
    return True, text, None

# ── Context window management ─────────────────────────────────────────────────
def trim_to_context(messages: list[dict], max_chars: int = MAX_HISTORY_CHARS) -> list[dict]:
    """
    Trim conversation history so total character count stays within budget.
    Always keeps the most recent messages; older ones are dropped first.
    The last user message is always preserved regardless of length.
    """
    total = 0
    result = []
    for msg in reversed(messages):
        total += len(msg.get("content", ""))
        if total > max_chars and result:
            break
        result.insert(0, msg)
    # Safety: always include at least the last message
    if not result and messages:
        result = [messages[-1]]
    return result

# ── Shared generation options ─────────────────────────────────────────────────
def _base_options(temperature: float) -> dict:
    return {
        "temperature": temperature,
        "top_p": 0.95,
        "top_k": 64,
        "num_predict": DEFAULT_NUM_PREDICT,
        "num_ctx": DEFAULT_NUM_CTX,
        "num_batch": DEFAULT_NUM_BATCH,
        "num_thread": NUM_THREADS,
        "use_mmap": USE_MMAP,
        "stop": ["Question:", "User:", "Asker:"],
    }

# ── AI Personality ────────────────────────────────────────────────────────────
SYSTEM_PERSONALITY = os.getenv(
    "AI_PERSONALITY",
    "Answer directly with specific facts, names, and examples. Do not repeat the question."
)

# ── App + Rate limiter ────────────────────────────────────────────────────────
def get_client_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return get_remote_address(request)


limiter = Limiter(key_func=get_client_key, default_limits=[RATE_LIMIT])


@asynccontextmanager
async def lifespan(_: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    global _http_client, _ollama_semaphore, _model_ready_lock
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=5.0),
        limits=httpx.Limits(
            max_connections=OLLAMA_NUM_PARALLEL + 8,
            max_keepalive_connections=OLLAMA_NUM_PARALLEL + 2,
            keepalive_expiry=30,
        ),
    )
    _ollama_semaphore = asyncio.Semaphore(OLLAMA_NUM_PARALLEL)
    _model_ready_lock = asyncio.Lock()
    logger.info(
        "HTTP client ready | parallel=%s | threads=%s | queue_limit=%s | queue_timeout=%ss",
        OLLAMA_NUM_PARALLEL,
        NUM_THREADS,
        MAX_QUEUE_DEPTH,
        QUEUE_TIMEOUT_SECONDS,
    )
    asyncio.create_task(_warmup_model())
    yield
    # ── Shutdown ──────────────────────────────────────────────────────────────
    if _http_client:
        await _http_client.aclose()


app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ── Shared resources ──────────────────────────────────────────────────────────
_http_client: httpx.AsyncClient | None = None
_ollama_semaphore: asyncio.Semaphore | None = None
_model_ready_lock: asyncio.Lock | None = None

# Tracks requests currently waiting for or holding a semaphore slot
_active_requests: int = 0
_running_requests: int = 0
_waiting_requests: int = 0

# Lightweight metrics
_metrics: dict = {
    "requests_total": 0,
    "requests_blocked_safety": 0,
    "requests_rate_limited": 0,
    "requests_queue_timeouts": 0,
    "requests_rejected_overload": 0,
    "requests_client_cancelled": 0,
    "avg_ttft_ms": 0.0,
    "ttft_samples": 0,
    "avg_queue_wait_ms": 0.0,
    "queue_wait_samples": 0,
    "total_tokens_streamed": 0,
}

_model_ready_cache: dict = {"result": False, "expires": 0.0}


class ClientDisconnected(Exception):
    """Raised when an SSE client disconnects before generation completes."""


async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    _metrics["requests_rate_limited"] += 1
    return _rate_limit_exceeded_handler(request, exc)


app.add_exception_handler(RateLimitExceeded, rate_limit_handler)


async def _warmup_model():
    """Load model into RAM before the first user request."""
    await asyncio.sleep(5)
    for attempt in range(20):
        try:
            r = await _http_client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                    "keep_alive": KEEP_ALIVE,
                    "options": {
                        "num_predict": 1,
                        "num_ctx": 512,
                        "num_batch": 16,
                        "num_thread": NUM_THREADS,
                        "use_mmap": USE_MMAP,
                    },
                },
            )
            if r.status_code == 200:
                _set_model_ready(True, ttl_seconds=30.0)
                logger.info("Model loaded into RAM and ready.")
                return
        except Exception as e:
            logger.info(f"Warmup attempt {attempt + 1}/20: {e}")
        await asyncio.sleep(10)
    logger.warning("Model warmup timed out — will load on first request")


# ── Validation error handler ──────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if errors:
        msg = str(errors[0].get("msg", "Validation error"))
        if msg.startswith("Value error, "):
            msg = msg.replace("Value error, ", "", 1)
        return JSONResponse(status_code=400, content={"error": msg})
    return JSONResponse(status_code=422, content={"error": "Invalid input"})


# ── Request models ────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str     # "user" | "assistant"
    content: str


class AskIn(BaseModel):
    messages: list[ChatMessage]
    temperature: float | None = DEFAULT_TEMPERATURE
    personality: str | None = None

    @field_validator('messages')
    @classmethod
    def validate_messages(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        if not v:
            raise ValueError("Messages cannot be empty")
        # Validate only the latest user message (previous ones were validated at send time)
        last_user = next((m for m in reversed(v) if m.role == 'user'), None)
        if last_user:
            validate_input(last_user.content)
        return v


class GenerateIn(BaseModel):
    prompt: str
    temperature: float | None = DEFAULT_TEMPERATURE

    @field_validator('prompt')
    @classmethod
    def validate_prompt(cls, v: str) -> str:
        return validate_input(v)


def _set_model_ready(result: bool, ttl_seconds: float = MODEL_READY_TTL_SECONDS):
    _model_ready_cache["result"] = result
    _model_ready_cache["expires"] = time.monotonic() + ttl_seconds


async def _is_model_ready(force: bool = False) -> bool:
    if _http_client is None:
        return False

    now = time.monotonic()
    if not force and now < _model_ready_cache["expires"]:
        return _model_ready_cache["result"]

    if _model_ready_lock is None:
        return _model_ready_cache["result"]

    async with _model_ready_lock:
        now = time.monotonic()
        if not force and now < _model_ready_cache["expires"]:
            return _model_ready_cache["result"]

        result = False
        try:
            r = await _http_client.get(f"{OLLAMA_URL}/api/tags", timeout=1.0)
            if r.status_code == 200:
                for m in r.json().get("models", []):
                    if m.get("name") == MODEL or m.get("name", "").startswith(MODEL):
                        result = True
                        break
        except httpx.HTTPError:
            result = False

        _set_model_ready(result)
        return result


_EMA_ALPHA = 0.1  # weight given to the latest sample (higher = more reactive)


def _update_ema(avg_key: str, samples_key: str, value: float):
    """Exponential moving average — stays responsive to recent changes."""
    if _metrics[samples_key] == 0:
        _metrics[avg_key] = value
    else:
        _metrics[avg_key] = _EMA_ALPHA * value + (1 - _EMA_ALPHA) * _metrics[avg_key]
    _metrics[samples_key] += 1


def _update_ttft(ttft_ms: float):
    _update_ema("avg_ttft_ms", "ttft_samples", ttft_ms)


def _update_queue_wait(wait_ms: float):
    _update_ema("avg_queue_wait_ms", "queue_wait_samples", wait_ms)


def _resolve_temperature(value: float | None) -> float:
    return DEFAULT_TEMPERATURE if value is None else value


@asynccontextmanager
async def _ollama_slot(request: Request | None = None):
    global _active_requests, _running_requests, _waiting_requests

    if _ollama_semaphore is None:
        raise HTTPException(503, detail="Model service unavailable.")
    if _waiting_requests >= MAX_QUEUE_DEPTH:
        _metrics["requests_rejected_overload"] += 1
        raise HTTPException(429, detail="Server is busy. Too many requests are queued.")

    acquired = False
    wait_started = time.monotonic()
    _active_requests += 1
    _waiting_requests += 1

    try:
        remaining = QUEUE_TIMEOUT_SECONDS
        while remaining > 0:
            if request is not None and await request.is_disconnected():
                _metrics["requests_client_cancelled"] += 1
                raise ClientDisconnected()

            step_started = time.monotonic()
            try:
                await asyncio.wait_for(
                    _ollama_semaphore.acquire(),
                    timeout=min(1.0, remaining),
                )
                acquired = True
                break
            except asyncio.TimeoutError:
                remaining -= time.monotonic() - step_started

        if not acquired:
            _metrics["requests_queue_timeouts"] += 1
            raise HTTPException(503, detail="Server is busy. Please retry in a few seconds.")

        _waiting_requests -= 1
        _running_requests += 1
        queue_wait_ms = (time.monotonic() - wait_started) * 1000
        _update_queue_wait(queue_wait_ms)
        yield round(queue_wait_ms)
    finally:
        if acquired:
            _running_requests -= 1
            _ollama_semaphore.release()
        else:
            _waiting_requests -= 1
        _active_requests -= 1


# ── Health / Readiness / Queue / Metrics ─────────────────────────────────────
@app.get("/health")
async def health():
    return {"ok": True, "model": MODEL, "model_ready": await _is_model_ready()}


@app.get("/ready")
async def ready():
    return {"ready": await _is_model_ready(), "model": MODEL}


@app.get("/queue")
def queue_status():
    """How many requests are currently waiting for an Ollama slot."""
    return {
        "active": _active_requests,
        "running": _running_requests,
        "capacity": OLLAMA_NUM_PARALLEL,
        "queued": _waiting_requests,
        "queue_limit": MAX_QUEUE_DEPTH,
    }


@app.get("/metrics")
async def metrics():
    return {
        **_metrics,
        "model": MODEL,
        "model_ready": await _is_model_ready(),
        "running": _running_requests,
        "queued": _waiting_requests,
        "capacity": OLLAMA_NUM_PARALLEL,
        "queue_limit": MAX_QUEUE_DEPTH,
    }


# ── /generate (raw prompt, non-streaming) ────────────────────────────────────
@app.post("/generate")
@limiter.limit(RATE_LIMIT)
async def generate(request: Request, payload: GenerateIn):  # noqa: ARG001 — slowapi needs `request`
    req_id = uuid.uuid4().hex[:8]
    logger.info(f"[{req_id}] Generate: {payload.prompt[:50]}...")
    _metrics["requests_total"] += 1

    if not await _is_model_ready():
        raise HTTPException(503, detail=f"Model '{MODEL}' is still loading.")

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": payload.prompt}],
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": _base_options(_resolve_temperature(payload.temperature)),
    }

    max_retries, retry_delay = 3, 2
    for attempt in range(max_retries):
        try:
            async with _ollama_slot() as queue_wait_ms:
                r = await _http_client.post(f"{OLLAMA_URL}/api/chat", json=body)
                r.raise_for_status()
                _set_model_ready(True)
                data = r.json()
                response_text = clean_llm_text(
                    data.get("message", {}).get("content", "").strip()
                )
                is_safe, validated_text, _ = validate_output(response_text)
                if not is_safe:
                    _metrics["requests_blocked_safety"] += 1
                    response_text = validated_text
                return {
                    "response": response_text or "I couldn't generate a proper response.",
                    "model": MODEL,
                    "queue_wait_ms": queue_wait_ms,
                }
        except HTTPException:
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error (attempt {attempt+1}): {e.response.status_code}")
            if attempt < max_retries - 1 and e.response.status_code == 500:
                await asyncio.sleep(retry_delay)
                continue
            raise HTTPException(503, detail="Model service temporarily unavailable.")
        except httpx.RequestError as e:
            logger.error(f"[{req_id}] Ollama connection error (attempt {attempt+1}): {e}")
            _set_model_ready(False, ttl_seconds=1.0)
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            raise HTTPException(503, detail="Cannot connect to model service.")
        except Exception as e:
            logger.error(f"[{req_id}] Unexpected error (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            raise HTTPException(500, detail="An unexpected error occurred.")


# ── /ask/stream — primary endpoint (SSE, multi-turn) ─────────────────────────
@app.post("/ask/stream")
@limiter.limit(RATE_LIMIT)
async def ask_stream(request: Request, payload: AskIn):
    """
    Multi-turn streaming chat via Ollama /api/chat.
    Sends the full conversation history so the model has context.
    Returns Server-Sent Events:
      {"type": "token",   "content": "..."}
      {"type": "done",    "ttft_ms": 123}
      {"type": "blocked", "refusal": "..."}
      {"type": "error",   "message": "..."}
    """
    req_id = uuid.uuid4().hex[:8]
    logger.info(f"[{req_id}] Stream: {len(payload.messages)} messages in history")
    _metrics["requests_total"] += 1

    if not await _is_model_ready():
        raise HTTPException(503, detail=f"Model '{MODEL}' is still loading.")

    personality = payload.personality or SYSTEM_PERSONALITY
    if len(personality) > 500:
        personality = personality[:500].rsplit('.', 1)[0] + '.'

    # Build messages for /api/chat: system prompt + trimmed history
    history = trim_to_context([{"role": m.role, "content": m.content} for m in payload.messages])
    chat_messages = [{"role": "system", "content": personality}] + history

    body = {
        "model": MODEL,
        "messages": chat_messages,
        "stream": True,
        "keep_alive": KEEP_ALIVE,
        "options": _base_options(_resolve_temperature(payload.temperature)),
    }

    async def event_stream():
        full_text  = ""
        request_start = time.monotonic()
        first_token_received = False
        max_retries, retry_delay = 3, 5

        try:
            for attempt in range(max_retries):
                try:
                    async with _ollama_slot(request) as queue_wait_ms:
                        async with _http_client.stream("POST", f"{OLLAMA_URL}/api/chat", json=body) as r:
                            r.raise_for_status()
                            _set_model_ready(True)
                            async for line in r.aiter_lines():
                                if await request.is_disconnected():
                                    _metrics["requests_client_cancelled"] += 1
                                    logger.info(f"[{req_id}] Stream client disconnected during generation")
                                    return
                                if not line.strip():
                                    continue
                                try:
                                    chunk = json.loads(line)
                                except json.JSONDecodeError:
                                    continue

                                token = chunk.get("message", {}).get("content", "")
                                done  = chunk.get("done", False)

                                if token:
                                    if not first_token_received:
                                        first_token_received = True
                                        ttft_ms = (time.monotonic() - request_start) * 1000
                                        _update_ttft(ttft_ms)

                                    full_text += token
                                    _metrics["total_tokens_streamed"] += 1
                                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

                                if done:
                                    is_safe, _, category = validate_output(full_text)
                                    if not is_safe:
                                        _metrics["requests_blocked_safety"] += 1
                                        refusal = REFUSAL_TEMPLATES.get(category, REFUSAL_TEMPLATES['default'])
                                        logger.error(f"[{req_id}] Stream output blocked [{category}]")
                                        yield f"data: {json.dumps({'type': 'blocked', 'refusal': refusal})}\n\n"
                                    else:
                                        ttft_ms = round((time.monotonic() - request_start) * 1000)
                                        yield f"data: {json.dumps({'type': 'done', 'ttft_ms': ttft_ms, 'queue_wait_ms': queue_wait_ms})}\n\n"
                                    return
                    return  # success, exit retry loop
                except ClientDisconnected:
                    logger.info("Stream client disconnected while waiting for a model slot")
                    return
                except HTTPException as e:
                    yield f"data: {json.dumps({'type': 'error', 'message': e.detail})}\n\n"
                    return
                except httpx.HTTPStatusError as e:
                    logger.error(f"Stream HTTP error (attempt {attempt+1}): {e.response.status_code}")
                    if attempt < max_retries - 1 and e.response.status_code in (500, 503):
                        await asyncio.sleep(retry_delay)
                        continue
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Model service temporarily unavailable.'})}\n\n"
                    return
                except httpx.RequestError as e:
                    logger.error(f"Stream connection error (attempt {attempt+1}): {e}")
                    _set_model_ready(False, ttl_seconds=1.0)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Cannot connect to model service.'})}\n\n"
                    return
        except Exception as e:
            logger.error(f"Stream unexpected error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'An unexpected error occurred.'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Static frontend ───────────────────────────────────────────────────────────
_dist = Path(__file__).parent / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(_dist / "index.html")

    @app.get("/{filename}", include_in_schema=False)
    async def serve_static_files(filename: str):
        file_path = _dist / filename
        if file_path.is_file() and file_path.name != "index.html":
            return FileResponse(file_path)
        return FileResponse(_dist / "index.html")


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
