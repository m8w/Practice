import ffmpeg
import os
import glob
import tkinter as tk
from tkinter import filedialog
import random
import time

def get_video_duration(video_path):
    """Get the duration of a video in seconds."""
    try:
        probe = ffmpeg.probe(video_path)
        duration = float(probe['streams'][0]['duration'])
        return duration
    except Exception as e:
        return None

def is_video_corrupted(video_path):
    """Check if video file is readable by ffmpeg."""
    try:
        probe = ffmpeg.probe(video_path)
        # Try to get basic info
        duration = float(probe['streams'][0]['duration'])
        # Try to extract a single frame at the start to verify it's readable
        (
            ffmpeg
            .input(video_path, ss=1)
            .output('pipe:', vframes=1, format='image2', vcodec='mjpeg')
            .run(capture_stdout=True, capture_stderr=True, quiet=True)
        )
        return False  # Video is OK
    except Exception:
        return True  # Video is corrupted

def extract_audio_mp3(video_path, output_path):
    """Extract audio as MP3 from video."""
    try:
        stream = ffmpeg.input(video_path)
        stream = ffmpeg.output(stream, output_path, acodec='libmp3lame', audio_bitrate='256k')
        ffmpeg.run(stream, overwrite_output=True, quiet=True)
        print(f"  ✓ MP3: {os.path.basename(output_path)}")
        time.sleep(0.5)  # Brief pause to keep system responsive
    except Exception as e:
        print(f"  Error creating MP3: {e}")

def extract_audio_wav_segments(video_path, output_folder, max_duration=10800):
    """Extract audio as WAV, splitting into 3-hour segments if needed."""
    duration = get_video_duration(video_path)
    
    if duration is None:
        print(f"  Error: Could not get video duration, skipping WAV extraction")
        return
    
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    
    if duration <= max_duration:
        # Single file
        output_path = os.path.join(output_folder, f"{base_name}.wav")
        try:
            stream = ffmpeg.input(video_path)
            stream = ffmpeg.output(stream, output_path, acodec='pcm_s16le', ac=2, ar='44100')
            ffmpeg.run(stream, overwrite_output=True, quiet=True)
            print(f"  ✓ WAV: {os.path.basename(output_path)}")
            time.sleep(0.5)  # Brief pause
        except Exception as e:
            print(f"  Error creating WAV: {e}")
    else:
        # Multiple segments
        num_segments = int(duration / max_duration) + 1
        print(f"  Audio is {duration/3600:.2f} hours, splitting into {num_segments} segments...")
        
        for i in range(num_segments):
            start_time = i * max_duration
            segment_duration = min(max_duration, duration - start_time)
            output_path = os.path.join(output_folder, f"{base_name}_part{i+1:02d}.wav")
            
            try:
                stream = ffmpeg.input(video_path, ss=start_time, t=segment_duration)
                stream = ffmpeg.output(stream, output_path, acodec='pcm_s16le', ac=2, ar='44100')
                ffmpeg.run(stream, overwrite_output=True, quiet=True)
                print(f"  ✓ WAV part {i+1}/{num_segments}: {os.path.basename(output_path)}")
                time.sleep(1)  # Longer pause between segments
            except Exception as e:
                print(f"  Error creating WAV segment {i+1}: {e}")

def extract_random_frames(video_path, output_folder):
    """Extract one random frame per 10 minutes of video."""
    duration = get_video_duration(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    
    if duration is None:
        return
    
    # Calculate number of 10-minute segments
    num_segments = int(duration / 600)  # 600 seconds = 10 minutes
    
    if num_segments == 0:
        num_segments = 1  # At least one frame for videos shorter than 10 minutes
    
    print(f"  Extracting {num_segments} random frame(s)...")
    
    for i in range(num_segments):
        # Define the time range for this segment
        segment_start = i * 600
        segment_end = min((i + 1) * 600, duration)
        
        # Pick a random timestamp within this segment
        random_timestamp = random.uniform(segment_start, segment_end)
        
        output_file = os.path.join(output_folder, f"{base_name}_frame_{i+1:03d}.jpg")
        
        try:
            (
                ffmpeg
                .input(video_path, ss=random_timestamp)
                .output(output_file, vframes=1, format='image2', vcodec='mjpeg')
                .overwrite_output()
                .run(quiet=True)
            )
        except ffmpeg.Error as e:
            print(f"  Error extracting frame: {e}")
    
    print(f"  ✓ Extracted {num_segments} frame(s)")

def extract_hourly_highlights(video_path, output_folder):
    """Extract 30-second random highlight from each hour of video."""
    duration = get_video_duration(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    
    if duration is None:
        return
    
    # Calculate number of hours
    num_hours = int(duration / 3600)  # 3600 seconds = 1 hour
    
    if num_hours == 0:
        num_hours = 1  # At least one highlight for videos shorter than 1 hour
    
    print(f"  Extracting {num_hours} 30-second highlight(s) (1 per hour)...")
    
    for i in range(num_hours):
        # Define the time range for this hour
        hour_start = i * 3600
        hour_end = min((i + 1) * 3600, duration)
        
        # Pick a random start time within this hour (ensuring 30 seconds fits)
        max_start = hour_end - 30
        if max_start < hour_start:
            max_start = hour_start
        
        random_start = random.uniform(hour_start, max_start)
        
        output_file = os.path.join(output_folder, f"{base_name}_highlight_{i+1:03d}.mp4")
        
        try:
            (
                ffmpeg
                .input(video_path, ss=random_start, t=30)
                .output(output_file, c='copy')
                .overwrite_output()
                .run(quiet=True)
            )
        except ffmpeg.Error as e:
            print(f"  Error extracting highlight: {e}")
    
    print(f"  ✓ Extracted {num_hours} highlight(s)")

def select_folder():
    """Open folder selection dialog."""
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    folder_path = filedialog.askdirectory(title="Select folder containing videos")
    root.destroy()
    return folder_path

def set_low_priority():
    """Set the process to low priority to not hog CPU."""
    try:
        # Lower the priority on macOS/Unix (19 is lowest priority)
        os.nice(19)  # Values from 0-20, 19 = lowest priority
        print("✓ Process priority set to LOWEST (nice value: 19)")
        print("  Your computer should remain responsive during processing")
        print("  The script will pause briefly between operations\n")
    except Exception as e:
        print(f"Note: Could not set low priority: {e}\n")

def main():
    print("=" * 70)
    print("VIDEO PROCESSOR")
    print("1. Extracts audio to MP3")
    print("2. Extracts audio to WAV (3-hour segments)")
    print("3. Extracts random frames (1 per 10 minutes)")
    print("4. Extracts 30-second highlights (1 per hour)")
    print("=" * 70)
    print()
    
    # Set process to low priority
    set_low_priority()
    
    # Select folder
    video_folder = select_folder()
    if not video_folder:
        print("No folder selected. Exiting.")
        return
    
    print(f"Selected folder: {video_folder}\n")
    
    # Create output folders
    mp3_folder = os.path.join(video_folder, "mp3")
    wav_folder = os.path.join(video_folder, "wav")
    images_folder = os.path.join(video_folder, "images")
    highlights_folder = os.path.join(video_folder, "highlights")
    
    os.makedirs(mp3_folder, exist_ok=True)
    os.makedirs(wav_folder, exist_ok=True)
    os.makedirs(images_folder, exist_ok=True)
    os.makedirs(highlights_folder, exist_ok=True)
    
    # Find video files
    video_extensions = ['*.mp4', '*.mov', '*.avi', '*.mkv']
    video_files = []
    for ext in video_extensions:
        video_files.extend(glob.glob(os.path.join(video_folder, ext)))
    
    if not video_files:
        print("No video files found.")
        return
    
    print(f"Found {len(video_files)} video file(s)\n")
    print("=" * 70)
    
    # Track corrupted files
    corrupted_files = []
    
    # Process each video
    for idx, video_file in enumerate(video_files, 1):
        base_name = os.path.splitext(os.path.basename(video_file))[0]
        
        print(f"\n{'='*70}")
        print(f"VIDEO {idx}/{len(video_files)}")
        print(f"{'='*70}")
        print(f"File: {os.path.basename(video_file)}")
        
        # Check if video is corrupted first
        if is_video_corrupted(video_file):
            print(f"⚠️  CORRUPTED FILE - Skipping")
            corrupted_files.append(os.path.basename(video_file))
            continue
        
        # Define expected output paths
        mp3_path = os.path.join(mp3_folder, f"{base_name}.mp3")
        wav_path = os.path.join(wav_folder, f"{base_name}.wav")
        
        # Check what already exists (safely handle missing folders)
        has_mp3 = os.path.exists(mp3_path)
        
        # Safe check for WAV files
        has_wav = False
        if os.path.exists(wav_folder):
            has_wav = os.path.exists(wav_path) or any(f.startswith(f"{base_name}_part") and f.endswith('.wav') for f in os.listdir(wav_folder))
        
        # Safe check for images
        has_images = False
        if os.path.exists(images_folder):
            has_images = any(f.startswith(base_name) and f.endswith('.jpg') for f in os.listdir(images_folder))
        
        # Safe check for highlights
        has_highlights = False
        if os.path.exists(highlights_folder):
            has_highlights = any(f.startswith(base_name) and '_highlight_' in f for f in os.listdir(highlights_folder))
        
        print(f"File: {os.path.basename(video_file)}")
        print(f"Status: MP3✓" if has_mp3 else "Status: MP3✗", end="")
        print(f" WAV✓" if has_wav else " WAV✗", end="")
        print(f" Images✓" if has_images else " Images✗", end="")
        print(f" Highlights✓" if has_highlights else " Highlights✗")
        
        # Count tasks to do
        tasks_needed = sum([not has_mp3, not has_wav, not has_images, not has_highlights])
        if tasks_needed == 0:
            print(f"\n✓ All outputs already exist, skipping this video")
            continue
        
        print(f"Tasks to complete: {tasks_needed}/4")
        
        # Step 1: Extract MP3 (if needed)
        if not has_mp3:
            print(f"\n[1/4] Extracting MP3...")
            extract_audio_mp3(video_file, mp3_path)
        else:
            print(f"\n[1/4] ✓ MP3 already exists, skipping...")
        
        # Step 2: Extract WAV (if needed)
        if not has_wav:
            print(f"[2/4] Extracting WAV (max 3 hours per file)...")
            extract_audio_wav_segments(video_file, wav_folder, max_duration=10800)
        else:
            print(f"[2/4] ✓ WAV file(s) already exist, skipping...")
        
        # Step 3: Extract frames (if needed)
        if not has_images:
            print(f"[3/4] Extracting random frames (1 per 10 minutes)...")
            extract_random_frames(video_file, images_folder)
        else:
            print(f"[3/4] ✓ Images already exist, skipping...")
        
        # Step 4: Extract highlights (if needed)
        if not has_highlights:
            print(f"[4/4] Extracting 30-second highlights (1 per hour)...")
            extract_hourly_highlights(video_file, highlights_folder)
        else:
            print(f"[4/4] ✓ Highlights already exist, skipping...")
        
        print(f"\n✓ Completed video {idx}/{len(video_files)}")
        
        # Pause between videos to keep system responsive
        if idx < len(video_files):
            time.sleep(2)
    
    print("\n" + "=" * 70)
    print("✓ ALL PROCESSING COMPLETE!")
    print(f"\nMP3 files: {mp3_folder}")
    print(f"WAV files: {wav_folder}")
    print(f"Random frames: {images_folder}")
    print(f"30-second highlights: {highlights_folder}")
    
    # Report corrupted files
    if corrupted_files:
        print("\n" + "=" * 70)
        print(f"⚠️  CORRUPTED FILES ({len(corrupted_files)}):")
        for corrupted in corrupted_files:
            print(f"  - {corrupted}")
        print("These files were skipped due to corruption.")
        print("=" * 70)

if __name__ == "__main__":
    main()
