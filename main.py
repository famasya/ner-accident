import requests
import pandas as pd
from tqdm import tqdm
import time
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import datetime
import logging
import json
from prompt import system_prompt

# Set up logging
logging.basicConfig(
    filename='processing_log.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def tokenize_and_save(tagged_data, output_prefix="partial"):
    """Tokenize the tagged data and save to file"""
    all_tokens_labels = []
    for idx, row in tagged_data.iterrows():
        data_id = row['index']
        tagged_text = row['tagged_full_text']
        
        if tagged_text == "ERROR" or pd.isna(tagged_text):
            logging.warning(f"Skipping tokenization for data ID {data_id} due to ERROR or empty value")
            continue
            
        try:
            soup = BeautifulSoup(tagged_text, "html.parser")

            for content in soup.contents:
                if content.name:  # It's a tag (e.g., <location>Jakarta</location>)
                    tag = content.name.upper()
                    tokens = content.text.strip().split()
                    for i, token in enumerate(tokens):
                        prefix = "B-" if i == 0 else "I-"
                        all_tokens_labels.append((data_id, token, f"{prefix}{tag}"))
                else:  # It's plain text
                    tokens = content.strip().split()
                    for token in tokens:
                        if token:
                            all_tokens_labels.append((data_id, token, "O"))
        except Exception as e:
            logging.error(f"Error tokenizing row {idx} (data ID {data_id}): {str(e)}")

    # Create DataFrame
    df = pd.DataFrame(all_tokens_labels, columns=["Data ID", "Token", "Label"])

    # Get the current timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Define the output filename with the timestamp
    output_filename = f"{output_prefix}_annotated_data_{timestamp}.csv"

    # Save the DataFrame to a CSV file
    df.to_csv(output_filename, index=False)
    
    logging.info(f"Tokenized data saved to: {output_filename}")
    print(f"Tokenized data saved to: {output_filename}")
    
    return df

def save_progress(df, filename="tagging_progress.csv"):
    """Save the current tagging progress to a file"""
    df.to_csv(filename, index=False)
    logging.info(f"Progress saved to {filename}")
    print(f"Progress saved to {filename}")

def load_progress(filename="tagging_progress.csv"):
    """Load the previous tagging progress if available"""
    if os.path.exists(filename):
        logging.info(f"Loading previous progress from {filename}")
        return pd.read_csv(filename)
    return None

def main():
    try:
        # Load the original data
        df = pd.read_csv('data.csv')
        aduan_texts_full = df[df['is_aduan'] == 1][['index','full_text']].dropna()

        # take first 1000 rows
        aduan_texts_full = aduan_texts_full.head(2)

        # load .env
        load_dotenv()

        # read api_key from .env
        api_key = os.getenv('API_KEY')

        # Check if we have previous progress
        progress_df = load_progress()
        
        if progress_df is not None:
            # Find which rows have already been processed
            processed_indices = set(progress_df['index'].values)
            
            # Filter out already processed rows
            aduan_texts_full = aduan_texts_full[~aduan_texts_full['index'].isin(processed_indices)]
            
            # If we have already processed all rows, use the progress data
            if len(aduan_texts_full) == 0:
                aduan_texts_full = progress_df
                logging.info("All rows were already processed. Using previous progress.")
            else:
                # Merge processed data with remaining data to process
                aduan_texts_full = pd.concat([progress_df, aduan_texts_full])
                logging.info(f"Resuming processing. {len(processed_indices)} rows already processed, {len(aduan_texts_full) - len(processed_indices)} remaining.")
        
        # Ensure tagged_full_text column exists
        if 'tagged_full_text' not in aduan_texts_full.columns:
            aduan_texts_full['tagged_full_text'] = None

        # TAGGING
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }    
        
        model = "google/gemini-2.5-flash-preview"

        # Chunk size for intermediate saves (save after every N rows)
        chunk_size = 10
        last_save = 0
        
        for idx, row in tqdm(aduan_texts_full.iterrows(), total=len(aduan_texts_full)):
            # Skip if already tagged
            if not pd.isna(aduan_texts_full.loc[idx, 'tagged_full_text']):
                continue
                
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
                        aduan_texts_full.loc[idx, 'tagged_full_text'] = content
                        print(f"generated [{row['index']}] : {content}")
                        logging.info(f"Successfully tagged row {idx} (data ID {row['index']})")
                        break
                    else:
                        error_msg = f"[{row['index']}] Error {response.status_code}: {response.text}"
                        print(error_msg)
                        logging.error(error_msg)
                except requests.exceptions.RequestException as e:
                    error_msg = f"[{row['index']}] Request failed (attempt {attempt + 1}): {e}"
                    print(error_msg)
                    logging.error(error_msg)
                    time.sleep(2)  # small delay before retry
            else:
                error_msg = f"All retries failed for row {idx} (data ID {row['index']})"
                aduan_texts_full.loc[idx, 'tagged_full_text'] = "ERROR"
                logging.error(error_msg)
            
            # Save progress periodically
            rows_processed = idx - last_save + 1
            if rows_processed >= chunk_size:
                save_progress(aduan_texts_full)
                
                # Also tokenize and save the chunk of newly tagged data
                chunk_data = aduan_texts_full.iloc[last_save:idx+1]
                tokenize_and_save(chunk_data, output_prefix="chunk")
                
                last_save = idx + 1

        # Save final progress
        save_progress(aduan_texts_full)
        
        # Final tokenization of all data
        final_tokens_df = tokenize_and_save(aduan_texts_full, output_prefix="final")

        print("Processing completed successfully!")

    except Exception as e:
        error_msg = f"Unhandled exception in main: {str(e)}"
        print(error_msg)
        logging.critical(error_msg, exc_info=True)

if __name__ == "__main__":
    main()
