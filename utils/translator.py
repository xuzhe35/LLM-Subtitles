import json
import time

def translate_segments(client, segments, target_lang, model="gpt-4o", batch_size=15):
    """
    Translates transcript segments using an LLM.
    Processes in batches to avoid token limits and manage context.
    """
    
    all_translated_segments = []
    total_batches = (len(segments) + batch_size - 1) // batch_size
    previous_context = [] 
    
    print(f"Total segments: {len(segments)}. Processing in {total_batches} batches of {batch_size}...")

    for i in range(0, len(segments), batch_size):
        batch_index = i // batch_size + 1
        print(f"Translating batch {batch_index}/{total_batches}...")
        
        batch = segments[i : i + batch_size]
        
        # Prepare simplified batch for LLM
        # We assign a temporary ID for the batch context
        simplified_batch = []
        for j, seg in enumerate(batch):
            simplified_batch.append({
                "id": j, # Local ID within batch
                "start": seg['start'],
                "end": seg['end'],
                "text": seg['text']
            })
            
        # Retry loop for reliability
        max_retries = 3
        attempt = 0
        translated_map = {}
        success = False

        while attempt < max_retries:
            try:
                translated_batch_list = _translate_batch_wrapper(client, simplified_batch, target_lang, model, previous_context)
                
                # Map by ID to ensure alignment even if LLM skips/merges
                translated_map = {item['id']: item['text'] for item in translated_batch_list if 'id' in item and 'text' in item}
                
                if len(translated_map) == len(batch):
                    success = True
                    break
                else:
                    print(f"  Warning: Batch {batch_index} incomplete ({len(translated_map)}/{len(batch)}). Retrying ({attempt+1}/{max_retries})...")
                    attempt += 1
                    time.sleep(1) # Wait a bit before retry
            except Exception as e:
                print(f"  Error translating batch {batch_index}: {e}. Retrying ({attempt+1}/{max_retries})...")
                attempt += 1
                time.sleep(1)

        if not success:
             print(f"  Failed to translate batch {batch_index} completely after retries. Attempting individual fallback for missing segments...")

        for j, orig_seg in enumerate(batch):
            # j is the local ID we assigned above
            if j in translated_map:
                trans_text = translated_map[j]
            else:
                # Attempt single-segment translation fallback
                print(f"    [Fallback] Translating missing segment {j}: \"{orig_seg['text'][:50]}...\"")
                trans_text = _translate_single_segment_fallback(client, orig_seg, target_lang, model)
                if trans_text != orig_seg['text']:
                    print(f"    [Fallback] Success: \"{trans_text[:50]}...\"")
                else:
                    print(f"    [Fallback] Failed, kept original.")
            
            all_translated_segments.append({
                "start": orig_seg['start'],
                "end": orig_seg['end'],
                "text": trans_text
            })
            
        # Update context for next batch (last 3 items of current batch)
        # We store the *Original* text for context, as that's what the LLM reads to understand the flow.
        # Ideally we'd pass both original and translated, but original is most critical for sentence completion.
        previous_context = [{
            "text": s['text'] 
        } for s in batch[-3:]]
        
        # Optional: Sleep briefly to be nice to rate limits if running very fast
        # time.sleep(0.5) 

    return all_translated_segments

def _translate_batch_wrapper(client, segments, target_lang, model, previous_context=None):
    context_str = ""
    if previous_context:
        context_lines = [item['text'] for item in previous_context]
        context_str = "\n".join(context_lines)
        
    system_prompt = f"""You are a professional subtitle translator. 
    Translate the following subtitle segments into {target_lang}.
    Maintain the original meaning, tone, and context.
    Output a JSON object with a single key 'segments' containing the list of translated segments.
    Each segment must have 'id', 'start', 'end', and 'text'.
    Do not change 'id', 'start', or 'end'.
    IMPORTANT: You MUST translate every single segment given to you. Do not skip any. Do not summarize.
    """
    
    user_content = {}
    if context_str:
        user_content["previous_context_for_reference_only"] = context_str
        
    user_content["segments_to_translate"] = segments
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_content, ensure_ascii=False)}
            ],
            response_format={ "type": "json_object" }
        )
        
        content = response.choices[0].message.content
        parsed = json.loads(content)
        return parsed.get('segments', [])
    except Exception as e:
        print(f"Error in translation wrapper: {e}")
        raise e

def _translate_single_segment_fallback(client, segment, target_lang, model):
    """
    Fallback: Translates a single segment individually.
    Used when batch translation fails or skips a segment.
    """
    max_fallback_retries = 2
    for attempt in range(max_fallback_retries):
        try:
            time.sleep(0.5)  # Brief delay to avoid rate limits
            
            system_prompt = f"""You are a professional subtitle translator.
Translate the following subtitle text into {target_lang}.
Maintain the original meaning and tone.
Output ONLY the translated text, no other commentary.
"""
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": segment['text']}
                ]
            )
            
            translated_text = response.choices[0].message.content.strip()
            if translated_text:
                return translated_text
            
        except Exception as e:
            print(f"    [Fallback Error] Attempt {attempt+1}/{max_fallback_retries} failed: {e}")
            time.sleep(1)
    
    print(f"    [Fallback] All attempts failed, keeping original text.")
    return segment['text'] # Ultimate fallback to original

