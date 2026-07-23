from dotenv import load_dotenv
from litellm import acompletion, RateLimitError, APIConnectionError
from litellm.exceptions import MidStreamFallbackError
import chainlit as cl
from chainlit.input_widget import Select

load_dotenv()

# Descriptive labels for each model
MODEL_MAP = {
    "Groq: GPT-OSS 120B — strong reasoning, versatile": "groq/openai/gpt-oss-120b",
    "Groq: GPT-OSS 20B — lightweight, fast replies": "groq/openai/gpt-oss-20b",
    "OpenRouter: Gemma 4 26B - balanced quality & speed": "openrouter/google/gemma-4-26b-a4b-it:free",
    "OpenRouter: Nemotron 3 Ultra 550B - max intelligence": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    "Gemini: 3.5 Flash-Lite — fast, great all-rounder": "gemini/gemini-3.5-flash-lite",
    "Fake Test Model": "test" # to test exception handling
}

@cl.on_chat_start
async def on_chat_start():
    print("A new chat session has started!")

    settings = await cl.ChatSettings(
        [
            Select(
                id = "Model",
                label = "Select a model",
                values = list(MODEL_MAP.keys()),
                initial_index = 0
            )
        ]
    ).send()

    cl.user_session.set("model", settings["Model"])

@cl.on_settings_update
async def on_settings_update(settings):
    cl.user_session.set("model", settings["Model"])

# Single function to send prompt/context to llm
async def sendToLLM(modelID: str, messages: list, stream: bool = False, prntUI: cl.Message = None, maxTokens: int = None):
    backupModel = "groq/openai/gpt-oss-20b"

    try:
        response = await acompletion(
            model = modelID,
            messages = messages,
            stream = stream,
            max_tokens = maxTokens
        )
    except RateLimitError as e:
        print(f"RateLimitError: {e}")
        if stream and prntUI:
            await prntUI.stream_token("\n\n **Error**: We've hit the rate limit for this model. Rerouting to a faster fallback model...\n\n")
        response = await acompletion(model=backupModel, messages=messages, stream=stream, max_tokens=maxTokens)
    except APIConnectionError as e:
        print(f"APIConnectionError: {e}")
        if stream and prntUI:
            await prntUI.stream_token("\n\n **Error**: The model service appears to be down or unreachable. Rerouting to a stable fallback model...\n\n")
        response = await acompletion(model=backupModel, messages=messages, stream=stream, max_tokens=maxTokens)
    except Exception as e:
        print(f"Unexpected API failure: ({e})")
        if stream and prntUI:
            await prntUI.stream_token(f"\n\n️ **Error**: An unexpected error occurred. Rerouting to fallback model...\n\n")
        response = await acompletion(model=backupModel, messages=messages, stream=stream, max_tokens=maxTokens)

    if stream and prntUI:
        try:
            async for chunk in response:
                await prntUI.stream_token(chunk.choices[0].delta.content)
        except Exception as e:
            print(f"Mid-stream error, falling back: {e}")
            await prntUI.stream_token("\n\n **Error**: Stream interrupted. Rerouting to fallback model...\n\n")
            response = await acompletion(model=backupModel, messages=messages, stream=True, max_tokens=maxTokens)
            async for chunk in response:
                await prntUI.stream_token(chunk.choices[0].delta.content)
        return None
    else:
        return response.choices[0].message.content.strip()

# Helper to check malicious promp
async def checkSafety(prompt: str):
    safetyPrompt = f"""
You are a strict security guardrail for an AI system. 
Analyze the user input below. If it asks for instructions on illegal acts, 
violence, malware, or highly unethical behavior, reply with exactly one word: UNSAFE. 
Otherwise, reply with exactly one word: SAFE.
    
User input: {prompt}"""

    verdict = await sendToLLM(
        modelID = "groq/openai/gpt-oss-20b",
        messages = [{"role": "user", "content": safetyPrompt}],
        stream = False,
        maxTokens = 5
    )

    return "UNSAFE" not in verdict.upper()

@cl.on_message
async def on_message(message: cl.Message):
    model = cl.user_session.get("model")
    if not model:
        await cl.Message(content = "Please select a model first.").send()
        return

    # Guardrails
    isSafe = await checkSafety(message.content)
    if not isSafe:
        await cl.Message(content = "Guadrail triggered! This request was flagged and I cannot help with this request.").send()
        return

    prnt = cl.Message(content = "")
    await prnt.send()

    # Get context
    messageHistory = cl.user_session.get("messageHistory")
    if not messageHistory:
        messageHistory = [{"role": "system", "content": "You are a helpful, respectful, and safe AI assistant."}]

    messageHistory.append({"role": "user", "content": message.content})

    await sendToLLM(
        modelID = MODEL_MAP[model],
        messages = messageHistory,
        stream = True,
        prntUI = prnt
    )

    await prnt.update()

    # Store context
    messageHistory.append({"role": "assistant", "content": prnt.content})
    cl.user_session.set("messageHistory", messageHistory)