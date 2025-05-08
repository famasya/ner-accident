import requests
import pandas as pd
from tqdm import tqdm
import time
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import datetime
from prompt import system_prompt

def main():
    df = pd.read_csv('data.csv')
    aduan_texts_full = df[df['is_aduan'] == 1][['index','full_text']].dropna()

    # take first 1000 rows
    aduan_texts_full = aduan_texts_full.head(1000)

    # sample aduan texts
    aduan_texts_sampled = aduan_texts_full.sample(n=4)
    print(aduan_texts_sampled)

    # load .env
    load_dotenv()

    # read api_key from .env
    api_key = os.getenv('API_KEY')

    # TAGGING
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }    
    
    model = "google/gemini-2.5-flash-preview"
    if 'tagged_full_text' not in aduan_texts_sampled.columns:
        aduan_texts_sampled['tagged_full_text'] = None

    for idx, row in tqdm(aduan_texts_sampled.iterrows(), total=len(aduan_texts_sampled)):
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": row['full_text']}
            ]
        }

        retries = 3
        for attempt in range(retries):
            try:
                response = requests.post(url, headers=headers, json=data, timeout=30)
                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"].strip()
                    aduan_texts_sampled.loc[idx, 'tagged_full_text'] = content
                    print(f"generated [{idx}] : {content}")
                    break
                else:
                    print(f"[{idx}] Error {response.status_code}: {response.text}")
            except requests.exceptions.RequestException as e:
                print(f"[{idx}] Request failed (attempt {attempt + 1}): {e}")
                time.sleep(2)  # small delay before retry
        else:
            aduan_texts_sampled.loc[idx, 'tagged_full_text'] = "ERROR"

    # TOKENIZATION
    all_tokens_labels = []
    for idx, row in tqdm(aduan_texts_sampled.iterrows(), total=len(aduan_texts_sampled)):
        tagged_text = row['tagged_full_text']

        soup = BeautifulSoup(tagged_text, "html.parser")

        for content in soup.contents:
            if content.name:  # It's a tag (e.g., <location>Jakarta</location>)
                tag = content.name.upper()
                tokens = content.text.strip().split()
                for i, token in enumerate(tokens):
                    prefix = "B-" if i == 0 else "I-"
                    all_tokens_labels.append((idx, token, f"{prefix}{tag}"))
            else:  # It's plain text
                tokens = content.strip().split()
                for token in tokens:
                    if token:
                        all_tokens_labels.append((idx, token, "O"))

    # Create DataFrame
    df = pd.DataFrame(all_tokens_labels, columns=["Data ID", "Token", "Label"])

    # Get the current timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Define the output filename with the timestamp
    output_filename = f"annotated_data_{timestamp}.csv"

    # Save the DataFrame to a CSV file in Google Drive
    df.to_csv(output_filename, index=False)

    print(f"DataFrame saved to: {output_filename}")


if __name__ == "__main__":
    main()
