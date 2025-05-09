from google import genai
from google.genai import types
import os
import logging
import json
from datetime import datetime
import asyncio

# Configure logging
log_directory = os.getenv("LOG_DIR", "logs")
os.makedirs(log_directory, exist_ok=True)
log_file = os.path.join(log_directory, f"llm_calls_{datetime.now().strftime('%Y%m%d')}.log")

# Set up logger
logger = logging.getLogger("llm_logger")
logger.setLevel(logging.INFO)
logger.propagate = False  # Prevent propagation to root logger
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# Simple cache configuration
# By default, we Google Gemini 2.5 pro, as it shows great performance for code understanding
async def call_llm_async(prompt):
    # Log the prompt
    logger.info(f"PROMPT: {prompt}")
    
    # Call the LLM
    # client = genai.Client(
    #     vertexai=True, 
    #     # TODO: change to your own project id and location
    #     project=os.getenv("GEMINI_PROJECT_ID", "your-project-id"),
    #     location=os.getenv("GEMINI_LOCATION", "us-central1")
    # )
    # You can comment the previous line and use the AI Studio key instead:
    client = genai.Client(
        api_key=os.getenv("GEMINI_API_KEY", "your-api_key"),
    )
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-preview-04-17")
    # model = os.getenv("GEMINI_MODEL", "gemini-2.5-pro-preview-03-25")
    
    # Use the async client method and await
    response = await client.aio.models.generate_content( 
        model=model,
        contents=[prompt]
    )
    response_text = response.text
    
    # Log the response
    logger.info(f"RESPONSE: {response_text}")
    
    return response_text

if __name__ == "__main__":
    test_prompt = "Give me a quick joke about a chicken."
    
    # Define an async main function to run the async call
    async def main():
        print("Making async call...")
        response1 = await call_llm_async(test_prompt)
        print(f"Response: {response1}")

    # Run the async main function
    asyncio.run(main())
    
