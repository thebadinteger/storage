import json
import os
import argparse
import sys

def parse_args():
    parser = argparse.ArgumentParser(description='[#] aisc - AI Studio Cleaner')
    parser.add_argument('-i', '--input', type=str, required=True, help='Input JSON file path')
    parser.add_argument('-n', '--number', type=int, default=0, help='Number of initial messages to skip')
    parser.add_argument('-o', '--output', type=str, required=True, help='Output TXT file path')
    return parser.parse_args()

def main():
    args = parse_args()
    
    input_path = args.input.strip("'\"")
    output_path = args.output.strip("'\"")
    skip_count = args.number

    if not os.path.exists(input_path):
        print(f"Error: Input file '{input_path}' not found.")
        return

    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        chunks = data.get('chunkedPrompt', {}).get('chunks', [])
        
        total_messages = len(chunks)
        total_tokens = sum(c.get('tokenCount', 0) for c in chunks)
        
        # Slicing logic
        cleaned_chunks = chunks[:skip_count]
        now_chunks = chunks[skip_count:]
        
        cleaned_messages = len(cleaned_chunks)
        cleaned_tokens = sum(c.get('tokenCount', 0) for c in cleaned_chunks)
        
        now_messages = len(now_chunks)
        now_tokens = sum(c.get('tokenCount', 0) for c in now_chunks)

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Process and Write
        print("[#] aisc")
        print(f"[=] Total: {total_messages} messages - {total_tokens} tokens")
        print(f"[-] Cleaned: {cleaned_messages} messages - {cleaned_tokens} tokens")
        print(f"[+] Now: {now_messages} messages - {now_tokens} tokens")

        with open(output_path, 'a', encoding='utf-8') as f_out:
            for chunk in now_chunks:
                role = chunk.get('role', 'unknown')
                text = chunk.get('text', '').replace('\n', ' ') # Keeping it on one line as requested
                is_thought = chunk.get('isThought', False)
                
                if role == 'user':
                    f_out.write(f'user: "{text}"\n')
                elif role == 'model':
                    if is_thought:
                        f_out.write(f'model_thinking: "{text}"\n')
                    else:
                        f_out.write(f'model: "{text}"\n')
            
        print("[!] Done!")

    except json.JSONDecodeError:
        print("Error: Failed to decode JSON.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()