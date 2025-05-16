# NOTE: Minimal code. No explicit error handling (e.g., try-except, file checks). Let it fail on missing assets.

import streamlit as st
import time
import base64
import os
import sqlite3 # Added
import asyncio # Add asyncio import
import random # Import random for tie-breaking
# import sys # Not used, can remove
from assets.texts import character_intros, character_names, monokuma_tutorial, character_profiles, game_introduction_text, hint_text # Import hint_text
from collections import Counter

# Import the flow creation function
# from flow import create_character_decision_flow # Keep the old name if needed, or remove if only parallel is used
from flow import create_character_decision_flow, create_parallel_decision_flow # Import both

# --- Page Config (MUST be the first Streamlit command) ---
st.set_page_config(
    page_title="Danganronpa Simulator",
    page_icon="./assets/monukuma_icon.webp", # Monokuma emoji or provide path to favicon
    initial_sidebar_state="collapsed",
)

# --- Basic SEO Meta Tags (Injected via Markdown) ---
st.markdown("""
    <meta name="description" content="Survive the Danganronpa killing game built with Pocket Flow! Investigate, debate, and vote in this interactive Streamlit app.">
    <meta name="keywords" content="Danganronpa, Pocket Flow, Streamlit, killing game, visual novel, AI, agentic coding, class trial">
    """, unsafe_allow_html=True)

# --- Constants for Viewer Modes ---
PLAYER_MODE_OPTION = ":small[🎮 **You Play:** Make choices, investigate, vote! Standard gameplay.]"
SHUICHI_VIEW_OPTION = ":small[🍿 **AI Plays (Character View):** AI decides actions. You watch from one character's perspective.]"
MONOKUMA_VIEW_OPTION = ":small[🔮 **AI Plays (Monokuma View):** AI decides actions. You watch with full info (secrets revealed!).]"

# --- Asset Path Helper ---
def get_asset_path(character_name, asset_type, emotion="normal"):
    """Constructs path to character assets. asset_type can be 'avatar' or 'audio'."""
    extension = "png" if asset_type == "avatar" else "wav"
    path = f"./assets/{character_name}/{emotion}.{extension}"
    return path

# --- Database Utility Functions ---
def init_db():
    """Initializes the in-memory SQLite database and returns the connection."""
    conn = sqlite3.connect(":memory:", check_same_thread=False) # Use check_same_thread=False for Streamlit
    cursor = conn.cursor()

    # Create roles table
    cursor.execute("""
    CREATE TABLE roles (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        role TEXT NOT NULL,
        is_alive BOOLEAN NOT NULL CHECK (is_alive IN (0, 1))
    )
    """)

    # Create actions table with emotion column
    cursor.execute("""
    CREATE TABLE actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day INTEGER NOT NULL,
        phase TEXT NOT NULL,
        actor_name TEXT NOT NULL,
        action_type TEXT NOT NULL,
        content TEXT,
        target_name TEXT,
        emotion TEXT
    )
    """)
    conn.commit()
    return conn

def assign_roles_and_log(conn, character_list):
    """Assigns roles, populates the roles table, and logs the assignment."""
    cursor = conn.cursor()
    num_players = len(character_list)
    # Example role distribution (adjust as needed)
    roles = ["Blackened"] * 3 + ["Truth-Seeker"] * 1 + ["Guardian"] * 1 + ["Student"] * (num_players - 5)
    random.shuffle(roles)

    for i, name in enumerate(character_list):
        role = roles[i]
        player_order = i + 1 # 1-based initial order
        # Insert into roles table
        cursor.execute(
            "INSERT INTO roles (id, name, role, is_alive) VALUES (?, ?, ?, ?)",
            (player_order, name, role, True)
        )
    conn.commit()

def tally_votes(individual_votes, tie_break_strategy='none'):
    """Tally votes based on plurality, handling Abstain and tie-breaking.

    Args:
        individual_votes (list): List of tuples (actor_name, target_name).
                                target_name can be None for Abstain.
        tie_break_strategy (str): 'none' (tie means no winner) or
                                  'random' (tie means random winner).

    Returns:
        str or None: The name of the winning target, or None if Abstain wins,
                     there's an unbreakable tie, or no votes were cast.
    """
    if not individual_votes:
        return None

    # Count votes for each target, including None (Abstain)
    vote_counts = Counter(target for _, target in individual_votes)

    if not vote_counts:
        return None # Should not happen if individual_votes is not empty, but safe check

    # Find the maximum vote count
    max_votes = 0
    for count in vote_counts.values():
        if count > max_votes:
            max_votes = count

    # Find all targets (including None) that received the maximum votes
    winners = [target for target, count in vote_counts.items() if count == max_votes]

    # --- Determine Outcome ---
    if None in winners:
        # Abstain received the highest votes (or tied), so no winner/target
        return None
    elif len(winners) == 1:
        # Exactly one non-abstain target received the most votes
        return winners[0]
    elif len(winners) > 1:
        # Tie between multiple non-abstain targets
        if tie_break_strategy == 'random':
            return random.choice(winners) # Randomly pick one of the tied winners
        else: # Default or 'none'
            return None # Tie means no winner
    else:
        # No winners found (e.g., only votes were for Abstain but it wasn't max?)
        # This case *shouldn't* be reachable given the logic, but default to None
        return None

# --- Session State Initialization ---
# Initialize only if keys don't exist
if 'current_state' not in st.session_state:
    # Core State
    st.session_state.current_state = "SHOW_PRE_GAME_OPTIONS" # Changed from IDLE
    st.session_state.current_day = 0
    st.session_state.db_conn = init_db() # Initialize DB
    st.session_state.task_queue = []
    st.session_state.user_character_name = "Shuichi" # Hardcoded assumption
    st.session_state.game_introduction_text = game_introduction_text # Load game intro text here
    st.session_state.current_phase_actors = None # NEW: Initialize to None
    st.session_state.user_input = None          # NEW: General user input
    st.session_state.inner_thought_submitted = False # NEW: Control submit button state

    # UI State
    st.session_state.buttons_used = set() # Will store "tutorial", "intro", "start_game" etc.

    # Game Data / Setup
    _temp_names = character_names[:]
    random.shuffle(_temp_names)
    st.session_state.shuffled_character_order = _temp_names
    assign_roles_and_log(st.session_state.db_conn, st.session_state.shuffled_character_order) # Assign roles

    # Static Game Info
    st.session_state.character_profiles = character_profiles
    st.session_state.hint_text = hint_text

    # Messages & Turn Tracking
    st.session_state.messages = []

    # Initial system message
    st.session_state.messages.append({
        "role": "Shuichi", # Assuming Shuichi is the user avatar initially
        "content": '*(You wake in a classroom with Monokuma and other students. No memory of arrival. The air crackles with tension. Unravel the mystery before despair takes hold.)* \n\n **Shuichi:** *"Uh... where am I? What is this place?"*',
        "emotion": "worried",
    })

# --- CSS ---
st.markdown(
    """
    <style>
        /* Avatar styling */
        div[data-testid^="stChatMessageAvatar"],
        div[data-testid="stChatMessage"] > div:first-child:not([data-testid^="stChatMessageAvatar"]),
        div.stChatMessage[data-testid="stChatMessage"] > img:first-child {
            width: 6rem !important; height: 6rem !important;
            min-width: 6rem !important; min-height: 6rem !important;
            flex-shrink: 0 !important; margin-right: 20px; margin-top: 20px;
        }
        div.stChatMessage[data-testid="stChatMessage"] > img:first-child {
            object-fit: cover !important; border-radius: 0.5rem !important;
        }
        /* Style for the button container below the chat */
        .button-container {
            display: flex;
            gap: 10px; /* Space between buttons */
            margin-top: 10px; /* Space above buttons */
            /* Buttons will naturally appear below the chat, no specific margin needed */
        }
    </style>
    """,
    unsafe_allow_html=True
)

# --- App Setup ---

st.image("./assets/banner.png", use_container_width=True)

st.caption(
    """
        Welcome to Hope's Peak Academy... where despair lurks behind every smile. 
        Can you survive the twisted killing game? Unmask the Blackened before it's too late! 
        Trust no one. Question everything. Your very life hangs in the balance.
    """,
)

# Add the open source footnote
st.markdown(
    """
    <div style="text-align: center; font-size: small;">
        This game is open source! Check out the project on <a href="https://github.com/The-Pocket/PocketFlow-Tutorial-Danganronpa-Simulator" target="_blank">GitHub</a>.
    </div>
    """,
    unsafe_allow_html=True
)

# Add a separator below the footnote
st.markdown("--- ")

# --- Function to generate HIDDEN autoplay audio HTML ---
def get_hidden_autoplay_html(file_path,errorLoop=False):
    """Generates minimal HTML for AUTOPLAY audio. ASSUMES file exists & is .wav"""
    # DON'T check if file exists first. Let it fail if it doesn't.
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode()
        mime_type = "audio/wav" # Assume wav for simplicity
        html = f"""
        <audio autoplay style="display:none">
            <source src="data:{mime_type};base64,{b64}" type="{mime_type}">
            Your browser does not support the audio element.
        </audio>
        """
    except:
        if errorLoop: return ""
        else: return get_hidden_autoplay_html(file_path="assets/Monokuma/think.wav",errorLoop=True)
        # Fail safely for missing audio, using a default file if possible
    
    return html

# --- Function to display a character message during processing ---
def display_interactive_message(character_name, content, emotion="normal", sleep_time=10, audio_path=None):
    """Displays a character's message with avatar, audio, text, and pause."""
    avatar_img_path = get_asset_path(character_name, "avatar", emotion)
    effective_audio_path = audio_path or get_asset_path(character_name, "audio", emotion)

    # Use a placeholder avatar if the real one isn't found
    display_avatar = avatar_img_path if avatar_img_path and os.path.exists(avatar_img_path) else "❓"

    with st.chat_message(character_name, avatar=display_avatar):
        # Generate audio HTML
        audio_html = get_hidden_autoplay_html(effective_audio_path) # Handles missing file
        if audio_html:
            st.markdown(audio_html, unsafe_allow_html=True)
        # Prepend character name in bold
        st.markdown(content)

    # Append to message history immediately after displaying
    st.session_state.messages.append({
        "role": character_name,
        "content": content, # Store the original content passed to the function
        "emotion": emotion,
    })

    if sleep_time > 0:
        time.sleep(sleep_time)

# --- Callback Function for Buttons (Simplified) ---
def handle_button_click(action_type, content):
    """Handles button clicks, populates task queue based on action_type."""
    # Mark action_type as used
    st.session_state.buttons_used.add(action_type)

    # Populate Task Queue based on action_type
    st.session_state.task_queue = [] # Clear previous queue first
    if action_type == 'intro':
        st.session_state.current_state = "RUNNING_INTRO"
        # Use the pre-shuffled list from session state
        character_list = st.session_state.shuffled_character_order
        total_intros = len(character_list)
        for i, character_name in enumerate(character_list):
            # Use content (assuming character_intros provides content based on name)
            intro_content = character_intros.get(character_name, "...")
            task = {
                'character_name': character_name,
                'content': f"({i + 1}/{total_intros}) **{character_name}:** {intro_content}",
                'emotion': 'normal',
                'sleep_time': 10,
                'audio_path': None
            }
            st.session_state.task_queue.append(task)

    elif action_type == 'tutorial':
        st.session_state.current_state = "RUNNING_TUTORIAL"
        total_tutorial_lines = len(monokuma_tutorial)
        for i, dialog_entry in enumerate(monokuma_tutorial):
            task = {
                'character_name': dialog_entry["speaker"],
                'content': f"({i + 1}/{total_tutorial_lines}) **{dialog_entry['speaker']}:** {dialog_entry['line']}",
                'emotion': dialog_entry.get("emotion", "normal"),
                'sleep_time': dialog_entry.get("sleep_time", 10),
                'audio_path': dialog_entry.get("audio_path")
            }
            st.session_state.task_queue.append(task)

# --- Callback for Start Game ---
def handle_start_game_click():
    """Handles the 'Start Game' button click."""
    st.session_state.buttons_used.add("start_game")
    st.session_state.current_day = 1 # Game starts on Day 1
    st.session_state.current_state = "GAME_START_INFO" # Changed from NIGHT_PHASE_BLACKENED
    st.session_state.current_phase_actors = None # Ensure actors list is reset for Day 1

# --- Callback for Continue after Role Reveal ---
def handle_continue_after_role_reveal():
    """Transitions state after user acknowledges role reveal."""
    st.session_state.current_state = "NIGHT_PHASE_BLACKENED_DISCUSSION"

# --- Function to display choice buttons ---
def display_pre_game_buttons():
    """Displays action choice buttons if their action_type hasn't been used and state is SHOW_PRE_GAME_OPTIONS."""
    if st.session_state.current_state == "SHOW_PRE_GAME_OPTIONS":
        # --- Row 1: Tutorial / Intro ---
        buttons_to_show_row1 = {
            "Talk to Monokuma": "tutorial",
            "Talk to Other Students": "intro"
        }
        available_buttons_row1 = {
            text: action for text, action in buttons_to_show_row1.items()
            if action not in st.session_state.buttons_used
        }

        if available_buttons_row1:
            cols = st.columns(len(available_buttons_row1))
            button_index = 0
            for btn_text, btn_action in available_buttons_row1.items():
                with cols[button_index]:
                    st.button(
                        btn_text,
                        key=f"choice_{btn_action}",
                        on_click=handle_button_click,
                        args=(btn_action, btn_text),
                        use_container_width=True,
                    )
                button_index += 1
        
        st.radio(
            "Select Character to Play:",
            options=character_names,
            key="user_name",
            index=0, # Default to Shuichi
            help=(
                "Choose a character to play as."
            )
        )

        # --- Row 2: Start Game ---
        if "start_game" not in st.session_state.buttons_used:
             st.button(
                 "Start Game",
                 key="start_game_button",
                 on_click=handle_start_game_click,
                 type="primary",
                 use_container_width=True
             )

             # --- Viewer Mode Radio Buttons ---
             st.radio(
                 "Select Game Mode:",
                 options=[
                     PLAYER_MODE_OPTION,
                     SHUICHI_VIEW_OPTION,
                     MONOKUMA_VIEW_OPTION
                 ],
                 key="viewer_mode_selection",
                 index=0, # Default to Player Mode
                 help=(
                     "Choose your experience: In Player Mode, you make key decisions for Shuichi. Alternatively, select a Viewer Mode where the AI plays all characters. Shuichi Perspective limits your view to what Shuichi knows, like watching a movie through his eyes. For full omniscience, choose Monokuma Perspective to see all secret discussions, investigation results, and protected targets."
                 )
             )

# --- Vote Summary Helper ---
def format_vote_summary(individual_votes):
    """Formats a list of votes into an aggregated summary string.

    Args:
        individual_votes (list): List of tuples (actor_name, target_name).
                                target_name can be None for Abstain.

    Returns:
        str: A formatted string summarizing the votes, or an empty string
             if no votes were cast.
    """
    if not individual_votes:
        return ""

    votes_by_target = {}
    for actor, target in individual_votes:
        target_display = target if target is not None else "Abstain"
        if target_display not in votes_by_target:
            votes_by_target[target_display] = []
        votes_by_target[target_display].append(actor)

    summary_lines = []
    # Sort targets alphabetically, potentially placing "Abstain" last or first if needed
    sorted_targets = sorted(votes_by_target.keys(), key=lambda x: (x == "Abstain", x))

    for target in sorted_targets:
        voters = votes_by_target[target]
        voter_list_str = ", ".join(sorted(voters)) # Sort voters for consistent output
        summary_lines.append(f"- **{target}** ({len(voters)}): {voter_list_str}")

    return "\n\n**Vote Breakdown:**\n" + "\n".join(summary_lines)

# --- Main Display Area ---
chat_container = st.container()

# --- Display Message History ---
with chat_container:
    for i, msg in enumerate(st.session_state.messages): 
        character_name = msg["role"]
        content = msg["content"]
        emotion = msg.get("emotion", "normal")
        avatar_path = get_asset_path(character_name, "avatar", emotion)
        avatar = avatar_path if avatar_path and os.path.exists(avatar_path) else "❓"
        name_to_display = character_name

        with st.chat_message(name=name_to_display, avatar=avatar):
            # Prepend display name in bold to the content
            # st.markdown("debug: " + content)
            st.markdown(content)

while True:
    # --- Process Task Queue if in a RUNNING state ---
    if st.session_state.current_state in ["RUNNING_TUTORIAL", "RUNNING_INTRO"]:
        tasks_to_process = st.session_state.task_queue[:]

        for task in tasks_to_process:
            display_interactive_message(
                character_name=task['character_name'],
                content=task['content'],
                emotion=task.get('emotion', 'normal'),
                sleep_time=task.get('sleep_time', 10),
                audio_path=task.get('audio_path')
            )

        st.session_state.task_queue = [] # Clear queue after processing

        st.session_state.current_state = "SHOW_PRE_GAME_OPTIONS" # Return to pre-game options
        st.rerun()

    # --- Pre-Game Button Display ---
    if st.session_state.current_state == "SHOW_PRE_GAME_OPTIONS":
        display_pre_game_buttons()
        break

    # --- Reveal User Role and Speaking Order ---
    if st.session_state.current_state == "GAME_START_INFO":
        # --- User Role Reveal ---
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        
        user_name = st.session_state.get("user_name", PLAYER_MODE_OPTION)
        st.session_state.user_character_name = user_name
        #user_name = "Shuichi" # Hardcoded user character name
        st.session_state.hint_text.replace("PLAYERCHARACTER",user_name) # Patch the AI's avoiding of the playing message

        # Assume Shuichi exists and has a role from the setup
        cursor.execute("SELECT role FROM roles WHERE name = ?", (user_name,))
        user_role = cursor.fetchone()[0] # Directly get the role

        role_message = f'**Monokuma:** 🌟 *"Ding-Dong-Dong-Ding! {user_name}, it turns out you\'re the **{user_role}**!"* \n\n'

        # Add role-specific instructions from Monokuma (medium length)
        if user_role == "Blackened":
            role_message += "😈 How exciting! Your goal is to secretly choose one victim each night with your fellow Blackened. Make sure you don't get caught during the Class Trial, or else it's Punishment Time!"
            # Find other Blackened players
            cursor.execute(
                "SELECT name FROM roles WHERE role = 'Blackened' AND is_alive = 1 AND name != ?",
                (user_name,)
            )
            other_blackened = [row[0] for row in cursor.fetchall()]
            if other_blackened:
                fellow_names = ", ".join(other_blackened)
                role_message += f"\n\nOh, and by the way, your delightful partners in crime this time around are: **{fellow_names}**! Don't tell anyone I told you, puhuhu..."
            else:
                role_message += "\n\nLooks like you're flying solo this time! No partners in crime for you... yet!"
        elif user_role == "Truth-Seeker":
            role_message += "🕵️ Ooh, a detective! Each night, pick one person to investigate. I'll secretly tell you if they are Blackened or just a Student. Use that info wisely during the trial!"
        elif user_role == "Guardian":
            role_message += "🛡️ A bodyguard, eh? Every night, choose one person to protect from the Blackened. Just remember, you can't protect the same person two nights in a row!"
        elif user_role == "Student":
            role_message += "🧑‍🎓 Well, looky here, just a regular Student! Your job is to survive! Pay attention during Class Trials, use your brain, and vote out the Blackened before they outnumber everyone else!"
        else:
            role_message += "Huh? What kinda role is that? Did I mess up? Nah, must be *your* fault! Just try not to die, okay?"

        # Display the role message interactively
        display_interactive_message(
            character_name="Monokuma",
            content=role_message,
            emotion="think",
            sleep_time=4,
            audio_path="./assets/dingdong.wav"
        )

        # Add role message to history
        st.session_state.messages.append({
            "role": "Monokuma",
            "content": role_message,
            "emotion": "think",
        })

        # Transition to the next state
        st.session_state.current_state = "NIGHT_PHASE_BLACKENED_DISCUSSION"

    # --- Night Phase: Blackened User Input ---
    if st.session_state.current_state == "NIGHT_PHASE_BLACKENED_USER_INPUT":
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        current_day = st.session_state.current_day
        user_character_name = st.session_state.user_character_name

        # Reset the submit button state when entering this state
        if not st.session_state.inner_thought_submitted:
             st.session_state.inner_thought_submitted = False

        # --- Display form --- #
        
        st.info(f"🤫 It's **{user_character_name}'s** turn to scheme! Whisper your wicked plans to guide **{user_character_name}** (100 chars max)!") # Modified line

        # Use a form to handle input and submission cleanly
        with st.form(key="blackened_input_form"):
            user_thought = st.text_input(
                f"Whisper your dark design... What's the plan for {user_character_name}? (100 chars max)",
                max_chars=100,
                key="blackened_thought_input",
                placeholder=f"Your input will *heavily* guide {user_character_name}. If left empty, {user_character_name} will decide themselves", # Modified placeholder
                disabled=st.session_state.inner_thought_submitted
            )
            submitted = st.form_submit_button(
                "Confirm Sinister Plot", # Changed button text
                type="primary", # Make button primary
                use_container_width=True, # Make button wide
                disabled=st.session_state.inner_thought_submitted # Disable after submit (redundant if form disappears, but safe)
            )

            if submitted:
                # Set the flag immediately and trigger a rerun to hide the form
                st.session_state.inner_thought_submitted = True
                st.rerun() # Force immediate re-render without the form
            elif not st.session_state.inner_thought_submitted:
                # --- If the form was NOT submitted AND we are not in the processing phase, break the loop ---
                break # Exit the while True loop and wait for user interaction

        # --- If form is submitted, continue to processing --- #
        if st.session_state.inner_thought_submitted:
            # --- This code now runs *outside the form* after a rerun if the form was submitted ---
            if st.session_state.inner_thought_submitted:
                user_thought = st.session_state.get("blackened_thought_input") # Get value from form state
                st.session_state.user_input = user_thought # Store for the flow

                # Run DecisionNode for the user, incorporating their input
                decision_flow = create_character_decision_flow()
                decision_flow.set_params({'character_name': user_character_name})
                # Pass the entire session state, node's prep will read user_input
                asyncio.run(decision_flow.run_async(st.session_state))

                # Retrieve the AI-generated statement (which considered user input)
                cursor.execute(
                    """SELECT content, emotion FROM actions
                        WHERE actor_name = ? AND day = ? AND phase = ? AND action_type = 'statement'
                        ORDER BY id DESC LIMIT 1""",
                    (user_character_name, current_day, "NIGHT_PHASE_BLACKENED_DISCUSSION") # Use the correct phase here
                )
                result = cursor.fetchone()
                user_statement = result[0] if result and result[0] else f"*({user_character_name} says nothing...)*"
                user_emotion = result[1] if result and result[1] else "blackened"

                # Display the user's AI-generated statement
                full_user_statement = f"**{user_character_name}:** {user_statement}"
                display_interactive_message(
                    character_name=user_character_name,
                    content=full_user_statement,
                    emotion=user_emotion,
                    sleep_time=3
                )

                # Cleanup and transition back
                st.session_state.user_input = None # Clear the user input
                st.session_state.inner_thought_submitted = False # Reset button state for next time
                st.session_state.current_state = "NIGHT_PHASE_BLACKENED_DISCUSSION"
                # NO rerun, let the main loop continue and re-enter CLASS_TRIAL_DISCUSSION
    
    # --- Night Phase: Blackened Discussion ---
    if st.session_state.current_state == "NIGHT_PHASE_BLACKENED_DISCUSSION":
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        current_day = st.session_state.current_day
        current_phase = st.session_state.current_state
        user_character_name = st.session_state.user_character_name
        # Get viewer mode selection directly
        viewer_mode_selection = st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION)

        # --- Initialization Logic --- (Runs only if current_phase_actors is None)
        if st.session_state.current_phase_actors is None:
            cursor.execute("SELECT name FROM roles WHERE role = 'Blackened' AND is_alive = 1 ORDER BY id") # Keep order consistent
            blackened_players = cursor.fetchall()
            st.session_state.current_phase_actors = [name for name, in blackened_players]

            if not st.session_state.current_phase_actors:
                # Handle case with no living Blackened
                print(f"Warning: No living Blackened found on Day {current_day}. Skipping discussion and vote.")
                st.session_state.current_phase_actors = None # Reset for next phase check
                st.session_state.current_state = "NIGHT_PHASE_TRUTH_SEEKER"
                # st.rerun() # script will continue to the vote block
            else:
                # Display Intro Message (once per day, based on viewer mode)
                user_is_blackened = False
                cursor.execute("SELECT role FROM roles WHERE name = ?", (user_character_name,))
                result = cursor.fetchone()
                if result and result[0] == 'Blackened':
                    user_is_blackened = True
                # Check viewer mode for display
                show_details = (viewer_mode_selection == MONOKUMA_VIEW_OPTION) or \
                               (user_is_blackened and (viewer_mode_selection == PLAYER_MODE_OPTION or viewer_mode_selection == SHUICHI_VIEW_OPTION))

                monokuma_intro_message = ""
                if show_details:
                    monokuma_intro_message = (
                        f'**Monokuma:** 😈 *"The time is 10 PM! Nighttime for Day {current_day} has officially begun... '
                        f'Blackened! Now it\'s YOUR time to shine! Get to plotting!"*'
                    )
                else: # Shuichi View or Player Mode (not Blackened)
                    monokuma_intro_message = (
                        f'**Monokuma:** 😈 *"The time is 10 PM! Nighttime for Day {current_day} has officially begun... '
                        f'... Killers, this is your time to strike! Everyone else... maybe say your prayers? Now hold your breath while the Blackened pick their lucky target!"*'
                    )

                display_interactive_message(
                    character_name="Monokuma", content=monokuma_intro_message, emotion="blackened", sleep_time=0.5, audio_path="./assets/night.wav"
                )
                # No rerun here, proceed directly to processing loop below

        # --- Processing Logic --- (Runs only if current_phase_actors is a list)
        if isinstance(st.session_state.current_phase_actors, list):
            transitioned_to_input = False # Flag to check if we broke for user input
            transitioned_to_vote = False # Flag to check if loop finished naturally
            while st.session_state.current_phase_actors: # Loop while actors remain
                # Pop the actor immediately
                current_actor = st.session_state.current_phase_actors.pop(0)

                # Check if user's turn in Player Mode
                if current_actor == user_character_name and viewer_mode_selection == PLAYER_MODE_OPTION:
                    # It's the user's turn in Player Mode
                    st.session_state.current_state = "NIGHT_PHASE_BLACKENED_USER_INPUT"
                    transitioned_to_input = True
                    break # Exit the while loop
                else:
                    # It's an AI's turn or Viewer Mode
                    # Run DecisionNode for the AI actor (Node decides based on its own perspective)
                    decision_flow = create_character_decision_flow()
                    decision_flow.set_params({'character_name': current_actor})
                    asyncio.run(decision_flow.run_async(st.session_state))

                    # Determine if the AI's statement should be DISPLAYED based on viewer mode
                    user_is_blackened = False
                    cursor.execute("SELECT role FROM roles WHERE name = ?", (user_character_name,))
                    result = cursor.fetchone()
                    if result and result[0] == 'Blackened':
                        user_is_blackened = True
                    # Check viewer mode for display
                    show_statement = (viewer_mode_selection == MONOKUMA_VIEW_OPTION) or \
                                     (user_is_blackened and (viewer_mode_selection == PLAYER_MODE_OPTION or viewer_mode_selection == SHUICHI_VIEW_OPTION))

                    if show_statement:
                        # Retrieve the statement (already logged by the node)
                        cursor.execute(
                            """SELECT content, emotion FROM actions
                                WHERE actor_name = ? AND day = ? AND phase = ? AND action_type = 'statement'
                                ORDER BY id DESC LIMIT 1""",
                            (current_actor, current_day, current_phase)
                        )
                        result = cursor.fetchone()
                        actor_speech = result[0] if result and result[0] else f"*({current_actor} says nothing, lost in thought...)*"
                        actor_emotion = result[1] if result and result[1] else "blackened"

                        # Display message
                        full_actor_message = f"**{current_actor}:** {actor_speech}"
                        display_interactive_message(
                            character_name=current_actor,
                            content=full_actor_message,
                            emotion=actor_emotion,
                            sleep_time=0.5 # No sleep in loop
                        )
                    # Else: Don't display the AI statement (e.g., Shuichi View and user is not Blackened)

                    # Loop continues to the next actor

            # --- After the loop --- #
            if transitioned_to_input:
                # Sleep after the loop finishes to allow the last message to be read
                time.sleep(7)
                st.rerun() # Rerun to enter the user input state
            elif not st.session_state.current_phase_actors: # Loop finished naturally
                st.session_state.current_phase_actors = None # Reset for next day
                st.session_state.current_state = "NIGHT_PHASE_BLACKENED_VOTE"
                # NO rerun here, main loop continues to the vote block

    # --- Night Phase: Blackened Vote --- (MODIFIED for User Input)
    if st.session_state.current_state == "NIGHT_PHASE_BLACKENED_VOTE":
        current_day = st.session_state.current_day
        current_phase = st.session_state.current_state
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name
        viewer_mode_selection = st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION)

        # Get all living Blackened players
        cursor.execute("SELECT name FROM roles WHERE role = 'Blackened' AND is_alive = 1")
        all_blackened_voters = [name for name, in cursor.fetchall()]

        # Check if user needs to vote
        user_is_blackened_voter = (user_character_name in all_blackened_voters)
        user_needs_to_vote = (user_is_blackened_voter and viewer_mode_selection == PLAYER_MODE_OPTION)

        next_state = None

        if user_needs_to_vote:
            # --- User Votes Scenario ---
            # Get list of AI Blackened voters (excluding user)
            ai_blackened_voters = [name for name in all_blackened_voters if name != user_character_name]

            # Run parallel flow for AI voters if any exist
            if ai_blackened_voters:
                st.session_state["acting_characters"] = ai_blackened_voters
                parallel_vote_flow = create_parallel_decision_flow()
                # Run async flow
                asyncio.run(parallel_vote_flow.run_async(st.session_state))
                st.session_state.pop("acting_characters", None) # Clean up

            # Transition to user input state
            st.session_state.current_state = "NIGHT_PHASE_BLACKENED_VOTE_USER_INPUT"
            st.rerun() # Rerun to process the next state immediately

        else:
            # --- AI Votes Only Scenario (or Viewer Mode) ---
            # Run parallel flow for ALL Blackened voters if any exist
            if all_blackened_voters:
                st.session_state["acting_characters"] = all_blackened_voters
                parallel_vote_flow = create_parallel_decision_flow()
                # Run async flow
                asyncio.run(parallel_vote_flow.run_async(st.session_state))
                st.session_state.pop("acting_characters", None) # Clean up

            # Transition directly to reveal state
            st.session_state.current_state = "NIGHT_PHASE_BLACKENED_VOTE_REVEAL"

    # --- Night Phase: Blackened Vote User Input --- (NEW STATE)
    if st.session_state.current_state == "NIGHT_PHASE_BLACKENED_VOTE_USER_INPUT":
        current_day = st.session_state.current_day
        current_phase = st.session_state.current_state # Log user vote in this phase
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name

        # Reset the submit button state flag if not already set (ensures form appears on first entry)
        if 'vote_form_submitted' not in st.session_state:
            st.session_state.vote_form_submitted = False

        # Get living players for voting options
        cursor.execute("SELECT name FROM roles WHERE is_alive = 1 ORDER BY id")
        living_player_names = [row[0] for row in cursor.fetchall()]
        vote_options = ["Abstain"] + living_player_names

        st.info(f"🔪 **Time to choose, {user_character_name}!** The shadows whisper... as a Blackened, cast your vote for tonight's victim.!")

        with st.form(key="blackened_vote_form"):
            selected_choice = st.selectbox(
                "Select your target (or Abstain):",
                options=vote_options,
                key="blackened_vote_choice", # Use a key to access value from session_state later
                index=0, # Default to Abstain
                disabled=st.session_state.vote_form_submitted # Disable after submission
            )
            vote_submitted_button = st.form_submit_button(
                "Confirm Target",
                type="primary",
                use_container_width=True,
                disabled=st.session_state.vote_form_submitted # Disable after submission
            )

            if vote_submitted_button:
                # Set flag immediately and rerun to hide the form
                st.session_state.vote_form_submitted = True
                st.rerun()
            elif not st.session_state.vote_form_submitted:
                # If the form was NOT submitted and we are not in the processing phase,
                # break the loop and wait for user interaction
                 break

        # --- Process the vote *after* the form is submitted and the rerun happened --- #
        if st.session_state.vote_form_submitted:
            # Retrieve the vote choice stored in session state by the radio button
            user_vote_choice = st.session_state.get("blackened_vote_choice", "Abstain")

            # Determine target_name (None if Abstain)
            target_name = None if user_vote_choice == "Abstain" else user_vote_choice

            # Log the user's vote using the MAIN phase name
            cursor.execute(
                """INSERT INTO actions (day, phase, actor_name, action_type, target_name)
                   VALUES (?, ?, ?, ?, ?) """,
                (current_day, 'NIGHT_PHASE_BLACKENED_VOTE', user_character_name, 'blackened_decision', target_name) # Use main phase
            )
            conn.commit()

            # Clean up the submission flag
            st.session_state.vote_form_submitted = False
            st.session_state.pop("blackened_vote_choice", None) # Clean up radio choice

            # Transition to reveal state
            st.session_state.current_state = "NIGHT_PHASE_BLACKENED_VOTE_REVEAL"
            # NO rerun here, let the main loop continue to the reveal state

    # --- Night Phase: Blackened Vote Reveal --- (NEW STATE - Replaces old consolidation logic)
    if st.session_state.current_state == "NIGHT_PHASE_BLACKENED_VOTE_REVEAL":
        current_day = st.session_state.current_day
        # Votes could have come from VOTE or VOTE_USER_INPUT phases
        possible_vote_phases = ['NIGHT_PHASE_BLACKENED_VOTE', 'NIGHT_PHASE_BLACKENED_VOTE_USER_INPUT']
        reveal_phase = st.session_state.current_state # Log reveal actions here
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name
        viewer_mode_selection = st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION)

        # Consolidate votes from all relevant phases for the current day
        vote_query = """SELECT actor_name, target_name FROM actions
                       WHERE day = ? AND phase = ? AND action_type = 'blackened_decision'
                       ORDER BY actor_name """
        cursor.execute(vote_query, (current_day, 'NIGHT_PHASE_BLACKENED_VOTE')) # Query only the main phase
        individual_blackened_votes = cursor.fetchall()

        # Tally votes
        final_blackened_target = tally_votes(individual_blackened_votes, tie_break_strategy='random')

        # Log the final decision
        cursor.execute(
            """INSERT INTO actions (day, phase, actor_name, action_type, target_name)
               VALUES (?, ?, ?, ?, ?) """,
            (current_day, reveal_phase, "Monokuma", "blackened_decision_final", final_blackened_target)
        )
        conn.commit()

        # --- Monokuma Summary (Conditional Display based on viewer mode) ---
        vote_list_str = ""
        if individual_blackened_votes:
            vote_list_str = format_vote_summary(individual_blackened_votes)
            # Log summary regardless of display
            cursor.execute(
                """INSERT INTO actions (day, phase, actor_name, action_type, content)
                 VALUES (?, ?, ?, ?, ?) """,
                (current_day, reveal_phase, "Monokuma", "blackened_vote_summary", vote_list_str)
            )
            conn.commit()

        # Determine DISPLAY visibility based on viewer mode
        user_is_blackened = False
        cursor.execute("SELECT role FROM roles WHERE name = ? AND is_alive = 1", (user_character_name,))
        result = cursor.fetchone()
        if result and result[0] == 'Blackened':
            user_is_blackened = True

        # Show target details only for Monokuma view or if user is Blackened (and in Player or Shuichi mode)
        show_target_details = (viewer_mode_selection == MONOKUMA_VIEW_OPTION) or \
                              (user_is_blackened and (viewer_mode_selection == PLAYER_MODE_OPTION or viewer_mode_selection == SHUICHI_VIEW_OPTION))

        # Construct summary message parts
        monokuma_vote_result_speech = ""
        if final_blackened_target:
            if show_target_details:
                monokuma_vote_result_speech = f"Puhuhu... The Blackened have reached a consensus! They've decided on **{final_blackened_target}** as their target!"
            else: # Player(not Blackened)/Shuichi perspective
                monokuma_vote_result_speech = f"Puhuhu... The Blackened have reached a consensus! They've made their choice... I wonder who the unlucky one is?"
        elif individual_blackened_votes: # No target chosen, but votes happened (likely abstain)
            monokuma_vote_result_speech = f"Puhuhu... The Blackened seem indecisive tonight, or perhaps they all abstained! No victim chosen!"
        else: # No Blackened were alive to vote
            monokuma_vote_result_speech = f"Yaaawn... No Blackened around means no creepy secret meetings tonight. How boring!"

        # Combine message parts conditionally
        full_monokuma_vote_summary = f'**Monokuma:** 😈 *"{monokuma_vote_result_speech}"*'.strip()
        # Only add detailed vote breakdown if showing target details AND breakdown exists
        if show_target_details and vote_list_str:
            full_monokuma_vote_summary += vote_list_str

        # Display the final message
        display_interactive_message(
            character_name="Monokuma",
            content=full_monokuma_vote_summary,
            emotion="blackened",
            sleep_time=6,
            audio_path="./assets/interesting.wav"
        )

        # Transition to the next state
        st.session_state.current_phase_actors = None # Reset actors list for next phase
        st.session_state.current_state = "NIGHT_PHASE_TRUTH_SEEKER"
        # No rerun needed here, main loop will continue

    # --- Night Phase: Truth-Seeker Investigation --- (MODIFIED for User Input)
    if st.session_state.current_state == "NIGHT_PHASE_TRUTH_SEEKER":
        current_day = st.session_state.current_day
        current_phase = st.session_state.current_state
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name
        viewer_mode_selection = st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION)

        cursor.execute("SELECT name FROM roles WHERE role = 'Truth-Seeker' AND is_alive = 1")
        truth_seeker_result = cursor.fetchone()

        if truth_seeker_result:
            truth_seeker_name = truth_seeker_result[0]
            user_is_truth_seeker = (user_character_name == truth_seeker_name)
            user_needs_to_investigate = (user_is_truth_seeker and viewer_mode_selection == PLAYER_MODE_OPTION)

            # --- Display Intro Message FIRST (if TS exists) ---
            monokuma_intro_speech = (
                '**Monokuma:** 🕵️ *"Puhuhu... Truth-Seeker, feeling curious tonight? Who do you want to investigate? Choose wisely!"*'
            )
            display_interactive_message(
                character_name="Monokuma", content=monokuma_intro_speech, emotion="think", sleep_time=0.5, audio_path="./assets/know.wav"
            )
            # --- End Intro Message ---

            # next_state = None # Removed this line
            if user_needs_to_investigate:
                # sleep for 3 seconds
                time.sleep(3)
                # Transition to user input state
                st.session_state.current_state = "NIGHT_PHASE_TRUTH_SEEKER_USER_INPUT"
                st.rerun() # Rerun to show the form
            else:
                # Run Decision Flow for AI Truth-Seeker
                decision_flow = create_character_decision_flow()
                decision_flow.set_params({'character_name': truth_seeker_name})
                # Run async flow
                asyncio.run(decision_flow.run_async(st.session_state))
                # DecisionNode logs the action, no need to retrieve target here
                st.session_state.current_state = "NIGHT_PHASE_TRUTH_SEEKER_REVEAL"
                # NO rerun here, main loop continues to the reveal state

            # Apply transition
            # st.session_state.current_state = next_state # Removed this line
            # st.rerun() # Removed this line

        else:
            # No living Truth-Seeker, skip to next phase
            st.session_state.current_state = "NIGHT_PHASE_GUARDIAN"
            # No rerun needed, main loop continues

    # --- Night Phase: Truth-Seeker User Input --- (NEW STATE)
    if st.session_state.current_state == "NIGHT_PHASE_TRUTH_SEEKER_USER_INPUT":
        current_day = st.session_state.current_day
        current_phase = st.session_state.current_state # Log user decision in this phase
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name
        truth_seeker_name = user_character_name # User is the truth seeker here

        # Reset the submit button state flag if not already set
        if 'ts_form_submitted' not in st.session_state:
            st.session_state.ts_form_submitted = False

        # Get living players for investigation options
        cursor.execute("SELECT name FROM roles WHERE is_alive = 1 ORDER BY id")
        living_player_names = [row[0] for row in cursor.fetchall()]
        # Truth-Seeker can investigate anyone, including self
        investigation_options = ["Abstain"] + living_player_names # Add Abstain option

        st.info(f"🕵️ **Your turn, {user_character_name}!** Point your magnifying glass. Who seems suspicious?")

        with st.form(key="truth_seeker_investigation_form"):
            selected_target = st.selectbox(
                "Select player to investigate:",
                options=investigation_options,
                key="ts_investigation_choice", # Key to access value later
                index=0, # Default to first player
                disabled=st.session_state.ts_form_submitted
            )
            investigation_submitted = st.form_submit_button(
                "Confirm Investigation Target",
                type="primary",
                use_container_width=True,
                disabled=st.session_state.ts_form_submitted
            )

            if investigation_submitted:
                # Set flag immediately and rerun to hide the form
                st.session_state.ts_form_submitted = True
                st.rerun()
            elif not st.session_state.ts_form_submitted:
                # Wait for user submission
                 break

        # --- Process the investigation *after* the form is submitted and rerun --- #
        if st.session_state.ts_form_submitted:
            # Retrieve the selected target
            user_investigation_choice = st.session_state.get("ts_investigation_choice", "Abstain") # Default to Abstain

            # Determine target_name (None if Abstain)
            target_name = None if user_investigation_choice == "Abstain" else user_investigation_choice

            # Log the user's decision using the MAIN phase name
            cursor.execute(
                """INSERT INTO actions (day, phase, actor_name, action_type, target_name)
                   VALUES (?, ?, ?, ?, ?) """,
                (current_day, 'NIGHT_PHASE_TRUTH_SEEKER', truth_seeker_name, 'truth_seeker_decision', target_name) # Use main phase
            )
            conn.commit()

            # Clean up the submission flag
            st.session_state.ts_form_submitted = False
            st.session_state.pop("ts_investigation_choice", None) # Clean up selectbox choice

            # Transition to reveal state
            st.session_state.current_state = "NIGHT_PHASE_TRUTH_SEEKER_REVEAL"
            # NO rerun here, let the main loop continue

    # --- Night Phase: Truth-Seeker Reveal --- (NEW STATE - Replaces old reveal logic)
    if st.session_state.current_state == "NIGHT_PHASE_TRUTH_SEEKER_REVEAL":
        current_day = st.session_state.current_day
        # Decision could have come from TRUTH_SEEKER or TRUTH_SEEKER_USER_INPUT phases
        possible_decision_phases = ['NIGHT_PHASE_TRUTH_SEEKER', 'NIGHT_PHASE_TRUTH_SEEKER_USER_INPUT']
        reveal_phase = st.session_state.current_state # Log reveal actions here
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name
        viewer_mode_selection = st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION)

        # Find the Truth-Seeker (should exist if we are in this phase)
        cursor.execute("SELECT name FROM roles WHERE role = 'Truth-Seeker' AND is_alive = 1")
        truth_seeker_result = cursor.fetchone()
        truth_seeker_name = truth_seeker_result[0] if truth_seeker_result else None

        investigation_target_name = None
        revealed_role_category = "Unknown"

        if truth_seeker_name:
            # Retrieve the decision from the database
            decision_query = """SELECT target_name FROM actions
                              WHERE actor_name = ? AND day = ? AND phase = ? AND action_type = 'truth_seeker_decision'
                              ORDER BY id DESC LIMIT 1"""
            cursor.execute(decision_query, (truth_seeker_name, current_day, 'NIGHT_PHASE_TRUTH_SEEKER')) # Query only the main phase
            investigation_result = cursor.fetchone()

            if investigation_result and investigation_result[0] is not None:
                investigation_target_name = investigation_result[0]
                cursor.execute("SELECT role FROM roles WHERE name = ? AND is_alive = 1", (investigation_target_name,))
                target_role_result = cursor.fetchone()
                if target_role_result:
                    target_role = target_role_result[0]
                    revealed_role_category = "Despair" if target_role == "Blackened" else "Hope"
                # Log private reveal (always happens, regardless of viewer mode)
                reveal_content = f"Investigated {investigation_target_name}, result: {revealed_role_category}"
                cursor.execute(
                    """INSERT INTO actions (day, phase, actor_name, action_type, target_name, content)
                        VALUES (?, ?, ?, ?, ?, ?) """,
                    (current_day, reveal_phase, "Monokuma", "reveal_role_private", truth_seeker_name, reveal_content)
                )
                conn.commit()

        # --- Construct and Display Second Message Conditionally (Based on viewer mode) ---
        monokuma_second_message_content = None # Initialize as None
        user_is_truth_seeker = (user_character_name == truth_seeker_name)

        # Determine who sees what
        show_publicly = (viewer_mode_selection == MONOKUMA_VIEW_OPTION)
        show_privately_to_user = user_is_truth_seeker and (viewer_mode_selection == PLAYER_MODE_OPTION or viewer_mode_selection == SHUICHI_VIEW_OPTION)

        if investigation_target_name: # If an investigation happened
            if show_publicly:
                monokuma_second_message_content = f'**Monokuma:** 🕵️ *"Puhuhu! Our little detective finished their snooping! It turns out **{investigation_target_name}** is on the **{revealed_role_category}** team!"*'
            elif show_privately_to_user:
                monokuma_second_message_content = f'**Monokuma:** 🕵️ *"Psst! You investigated **{investigation_target_name}**... They belong to the **{revealed_role_category}** team! Keep it zipped!"*'
            # Else: Don't display anything (e.g., Player not TS, or Shuichi View not TS)
        else: # No investigation target chosen or TS not found
            # Only show this if viewer is Monokuma or the user IS the TS
            if show_publicly or show_privately_to_user:
                 # Display slightly different message if TS exists but didn't choose
                 no_target_msg = "Hmm? The Truth-Seeker decided to take the night off? Lazy bum!" if truth_seeker_name else "No Truth-Seeker around to snoop tonight!"
                 monokuma_second_message_content = f'**Monokuma:** 🕵️ *"{no_target_msg}"*'

        # Display the constructed second message ONLY if content was generated
        if monokuma_second_message_content:
            display_interactive_message(
                character_name="Monokuma", content=monokuma_second_message_content, emotion="normal", sleep_time=7, audio_path="./assets/mystery.wav"
            )
        elif not truth_seeker_name:
            # If no TS exists, add a small pause anyway before proceeding
            #  time.sleep(1)
            pass

        # Transition to next phase
        st.session_state.current_state = "NIGHT_PHASE_GUARDIAN"
        # No rerun needed, main loop continues

    # --- Night Phase: Guardian Protection --- (MODIFIED for User Input)
    if st.session_state.current_state == "NIGHT_PHASE_GUARDIAN":
        current_day = st.session_state.current_day
        current_phase = st.session_state.current_state
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name
        viewer_mode_selection = st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION)

        cursor.execute("SELECT name FROM roles WHERE role = 'Guardian' AND is_alive = 1")
        guardian_result = cursor.fetchone()

        if guardian_result:
            guardian_name = guardian_result[0]
            user_is_guardian = (user_character_name == guardian_name)
            user_needs_to_protect = (user_is_guardian and viewer_mode_selection == PLAYER_MODE_OPTION)

            # --- Display Intro Message FIRST (if Guardian exists) ---
            monokuma_intro_speech = (
                '**Monokuma:** 🛡️ *"Alright, Guardian! Choose someone to shield from the inevitable despair! Just don\'t pick the same person twice in a row, okay?"*'
            )
            display_interactive_message(
                character_name="Monokuma", content=monokuma_intro_speech, emotion="worried", sleep_time=0.5, audio_path="./assets/important.wav"
            )
            # --- End Intro Message ---

            # next_state = None # Removed this line
            if user_needs_to_protect:
                # sleep for 10 seconds
                time.sleep(10)
                # Transition to user input state
                st.session_state.current_state = "NIGHT_PHASE_GUARDIAN_USER_INPUT"
                st.rerun() # Rerun to show the form
            else:
                # Run Decision Flow for AI Guardian
                decision_flow = create_character_decision_flow()
                decision_flow.set_params({'character_name': guardian_name})
                # Run async flow
                asyncio.run(decision_flow.run_async(st.session_state))
                # DecisionNode logs the action
                st.session_state.current_state = "NIGHT_PHASE_GUARDIAN_REVEAL"
                # NO rerun here, main loop continues to the reveal state

            # Apply transition
            # st.session_state.current_state = next_state # Removed this line
            # st.rerun() # Removed this line

        else:
            # No living Guardian, skip to next phase
            st.session_state.current_state = "MORNING_ANNOUNCEMENT"
            # No rerun needed, main loop continues

    # --- Night Phase: Guardian User Input --- (NEW STATE)
    if st.session_state.current_state == "NIGHT_PHASE_GUARDIAN_USER_INPUT":
        current_day = st.session_state.current_day
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name
        guardian_name = user_character_name # User is the guardian

        # Reset the submit button state flag
        if 'g_form_submitted' not in st.session_state:
            st.session_state.g_form_submitted = False

        # Get living players
        cursor.execute("SELECT name FROM roles WHERE is_alive = 1 ORDER BY id")
        living_player_names = [row[0] for row in cursor.fetchall()]

        # Get last protected target to exclude from options
        last_protected_target = None
        if current_day > 1:
            cursor.execute(
                """SELECT target_name FROM actions
                   WHERE actor_name = ? AND action_type = 'guardian_decision' AND day = ?
                   ORDER BY id DESC LIMIT 1""",
                (guardian_name, current_day - 1)
            )
            target_result = cursor.fetchone()
            if target_result:
                last_protected_target = target_result[0]

        # Filter options (Guardian can protect self)
        protection_options = [name for name in living_player_names if name != last_protected_target]
        protection_options_with_abstain = ["Abstain"] + protection_options # Add Abstain option

        # Check if there are any valid options left
        if not protection_options:
            st.warning(f"⚠️ **{user_character_name}, you protected {last_protected_target} last night!** You must choose someone else, but there's no one else left to protect. You cannot protect anyone this night.")
            # Log an implicit 'no protection' action? (Handled by reveal finding no decision)
            # For now, just transition directly
            st.session_state.g_form_submitted = False # Ensure flag is reset if needed elsewhere
            st.session_state.current_state = "NIGHT_PHASE_GUARDIAN_REVEAL"
            # st.rerun() # REMOVED: Let loop continue directly to reveal
        else:
            info_text = f"🛡️ **Guardian {user_character_name}, act fast!** Who needs your protection tonight?" 
            if last_protected_target:
                info_text += f"\n\n (Remember, **no repeats!** You can't pick {last_protected_target}.)"
            st.info(info_text)

            with st.form(key="guardian_protection_form"):
                selected_target = st.selectbox(
                    "Select player to protect (or Abstain):", # Update label
                    options=protection_options_with_abstain, # Use list with Abstain
                    key="g_protection_choice",
                    index=0, # Default to Abstain
                    disabled=st.session_state.g_form_submitted
                )
                protection_submitted = st.form_submit_button(
                    "Confirm Protection Target",
                    type="primary",
                    use_container_width=True,
                    disabled=st.session_state.g_form_submitted
                )

                if protection_submitted:
                    # Set flag immediately and rerun
                    st.session_state.g_form_submitted = True
                    st.rerun()
                elif not st.session_state.g_form_submitted:
                    # Wait for user submission
                    break

            # --- Process the protection *after* form submission and rerun --- #
            if st.session_state.g_form_submitted:
                # Retrieve selected target
                user_protection_choice = st.session_state.get("g_protection_choice", "Abstain") # Default to Abstain

                # Determine target_name (None if Abstain)
                target_name = None if user_protection_choice == "Abstain" else user_protection_choice

                # Log the user's decision (target_name will be None if Abstain)
                cursor.execute(
                    """INSERT INTO actions (day, phase, actor_name, action_type, target_name)
                    VALUES (?, ?, ?, ?, ?) """,
                    (current_day, "NIGHT_PHASE_GUARDIAN", guardian_name, 'guardian_decision', target_name)
                )
                conn.commit()

                # Clean up
                st.session_state.g_form_submitted = False
                st.session_state.pop("g_protection_choice", None)

                # Transition
                st.session_state.current_state = "NIGHT_PHASE_GUARDIAN_REVEAL"
                # NO rerun needed, main loop continues

    # --- Night Phase: Guardian Reveal --- (NEW STATE - Replaces old reveal logic)
    if st.session_state.current_state == "NIGHT_PHASE_GUARDIAN_REVEAL":
        current_day = st.session_state.current_day
        reveal_phase = st.session_state.current_state
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name
        viewer_mode_selection = st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION)

        # Find the Guardian
        cursor.execute("SELECT name FROM roles WHERE role = 'Guardian' AND is_alive = 1")
        guardian_result = cursor.fetchone()
        guardian_name = guardian_result[0] if guardian_result else None

        protected_target = None
        if guardian_name:
            # Retrieve the decision from the database
            decision_query = """SELECT target_name FROM actions
                              WHERE actor_name = ? AND day = ? AND phase = ? AND action_type = 'guardian_decision'
                              ORDER BY id DESC LIMIT 1"""
            cursor.execute(decision_query, (guardian_name, current_day, 'NIGHT_PHASE_GUARDIAN')) # Query only the main phase
            protection_result = cursor.fetchone()
            if protection_result and protection_result[0] is not None:
                protected_target = protection_result[0]

        # --- Construct and Display Second Message Conditionally (Based on viewer mode) ---
        monokuma_second_message_content = None # Initialize as None
        user_is_guardian = (user_character_name == guardian_name)

        # Determine who sees what
        show_publicly = (viewer_mode_selection == MONOKUMA_VIEW_OPTION)
        show_privately_to_user = user_is_guardian and (viewer_mode_selection == PLAYER_MODE_OPTION or viewer_mode_selection == SHUICHI_VIEW_OPTION)

        if protected_target: # If protection happened
                if show_publicly:
                    monokuma_second_message_content = f'**Monokuma:** 🛡️ *"Alright, alright! The Guardian has made their choice! Looks like **{protected_target}** gets a bodyguard tonight! Lucky them!"*'
                elif show_privately_to_user:
                    monokuma_second_message_content = f'**Monokuma:** 🛡️ *"Alright, you chose to protect **{protected_target}** tonight. Sweet dreams!"*'
                # Else: Don't display anything
        else: # No protection target chosen or Guardian not found
                # Only show if viewer is Monokuma or the user IS the Guardian (if they exist)
                if show_publicly or show_privately_to_user:
                    no_protect_msg = "What's this? The Guardian didn't protect anyone? Feeling risky, are we?" if guardian_name else "No Guardian around to protect anyone tonight!"
                    monokuma_second_message_content = f'**Monokuma:** 🛡️ *"{no_protect_msg}"*'

        # Display the determined second message ONLY if content was generated
        if monokuma_second_message_content:
            display_interactive_message(
                character_name="Monokuma", content=monokuma_second_message_content, emotion="normal", sleep_time=4, audio_path="./assets/thank.wav"
            )
        elif not guardian_name:
             # If no Guardian exists, add a small pause anyway
             time.sleep(1)

        # Transition to next phase
        st.session_state.current_state = "MORNING_ANNOUNCEMENT"
        # No rerun needed, main loop continues

    # --- Morning Announcement: Resolve Night Actions ---
    if st.session_state.current_state == "MORNING_ANNOUNCEMENT":
        # --- Monokuma Intro ---
        monokuma_intro_speech = (
            '**Monokuma:** ☀️ *"Rise and shine, kiddos! It\'s another gorgeous day for a KILLING GAME! Puhuhu! Let\'s see what happened while you were all snug in your beds..."*'
        )
        display_interactive_message(
            character_name="Monokuma",
            content=monokuma_intro_speech,
            emotion="blackened",
            sleep_time=10,
            audio_path="./assets/day.wav"
        )

        current_day = st.session_state.current_day
        # Note: current_phase is the *start* of the morning, not a specific night phase
        current_phase = st.session_state.current_state
        conn = st.session_state.db_conn
        cursor = conn.cursor()

        # --- Determine Final Blackened Target (Retrieve from DB) ---
        # The final decision was logged at the end of NIGHT_PHASE_BLACKENED_VOTE
        final_blackened_target = None
        cursor.execute(
            """SELECT target_name FROM actions
            WHERE day = ? AND phase = ? AND action_type = 'blackened_decision_final'
            ORDER BY id DESC LIMIT 1""",
            (current_day, 'NIGHT_PHASE_BLACKENED_VOTE_REVEAL') # Corrected phase
        )
        result = cursor.fetchone()
        if result:
            final_blackened_target = result[0]

        # --- Get Guardian Protection Target ---
        protected_target = None
        cursor.execute(
            """SELECT target_name FROM actions
            WHERE day = ? AND phase = ? AND action_type = 'guardian_decision' AND target_name IS NOT NULL
            ORDER BY id DESC LIMIT 1""",
            (current_day, 'NIGHT_PHASE_GUARDIAN') # Simplified phase
        )
        protection_result = cursor.fetchone()
        if protection_result:
            protected_target = protection_result[0]

        # --- Resolve Kill Attempt ---
        victim_name = None
        kill_successful = False
        victim_role = None # Store the victim's role

        if final_blackened_target:
            if final_blackened_target != protected_target:
                # Kill successful
                victim_name = final_blackened_target
                kill_successful = True

                # Get victim's role BEFORE updating status
                cursor.execute("SELECT role FROM roles WHERE name = ?", (victim_name,))
                role_result = cursor.fetchone()
                if role_result:
                    victim_role = role_result[0]

                # Update victim's status in the database
                cursor.execute("UPDATE roles SET is_alive = ? WHERE name = ?", (False, victim_name))
                conn.commit()

                action_to_log = "kill"
                target_for_log = victim_name
                content_for_log = f"Victim Role: {victim_role}"
            else:
                # Target was protected - treat as generic safe night
                kill_successful = False
                action_to_log = "safe_night"
                target_for_log = None # Keep consistent with no target chosen
                content_for_log = None # Keep consistent with no target chosen
        else:
            # No one was targeted
            kill_successful = False
            action_to_log = "safe_night"
            target_for_log = None
            content_for_log = None

        # --- Display Outcome ---
        if kill_successful:

            # 1. Display Victim Message
            victim_death_message = f'*After a long night, you find the lifeless body of **{victim_name}**, who turned out to be the **{victim_role}**!*'
            display_interactive_message(
                character_name=victim_name, # Use victim's name
                content=victim_death_message,
                emotion="death", # Use 'death' emotion
                sleep_time=8,
                audio_path="./assets/despair.wav" # Use despair audio
            )       

            # --- Player Death Check (Moved After Message) ---
            if victim_name == st.session_state.user_character_name and st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION) == PLAYER_MODE_OPTION:
                st.error("💀 **You have been killed!** Though your part in the game is over, the show must go on! You can keep watching to see how things unfold for the remaining participants.")
            # --- End Player Death Check ---

        else:
            # No kill - Monokuma is bored
            monokuma_announcement = '*"Yaaawn... Another peaceful night passes. HOW UTTERLY BORING! No murders to report!"*'
            full_monokuma_announce = f'**Monokuma:** {monokuma_announcement}'
            display_interactive_message(
                character_name="Monokuma",
                content=full_monokuma_announce,
                emotion="worried", # Use worried emotion
                sleep_time=8,
                audio_path="./assets/lowenergy.wav"
            )

        # Log the outcome
        cursor.execute(
            """INSERT INTO actions (day, phase, actor_name, action_type, target_name, content)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (current_day, current_phase, "Monokuma", action_to_log, target_for_log, content_for_log)
        )
        conn.commit()

        # --- Check Win Conditions ---
        cursor.execute("SELECT COUNT(*) FROM roles WHERE role = 'Blackened' AND is_alive = 1")
        living_blackened_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM roles WHERE role != 'Blackened' AND is_alive = 1")
        living_others_count = cursor.fetchone()[0]

        next_state = None
        if living_blackened_count == 0:
            next_state = "GAME_OVER_HOPE"
        elif living_blackened_count >= living_others_count:
            next_state = "GAME_OVER_DESPAIR"
        else:
            st.session_state.current_phase_actors = None # Reset actors list for next phase
            next_state = "CLASS_TRIAL_DISCUSSION"

        # Transition to the next state
        st.session_state.current_state = next_state

    # --- Class Trial: User Input ---
    if st.session_state.current_state == "CLASS_TRIAL_USER_INPUT":
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        current_day = st.session_state.current_day
        user_character_name = st.session_state.user_character_name

        # Reset the submit button state when entering this state
        if not st.session_state.inner_thought_submitted:
             st.session_state.inner_thought_submitted = False

        # --- Display form --- #
        st.info(f"⚖️ It's **{user_character_name}'s** turn to speak up! Lay out the case to guide **{user_character_name}** (100 chars max)!") # Modified line

        # Use a form for clean input handling
        with st.form(key="trial_input_form"):
            user_trial_input = st.text_input(
                "Unravel the truth! What's your key argument for {user_name}? (100 chars max)",
                max_chars=100,
                key="trial_thought_input",
                placeholder=f"Your input will *heavily* guide {user_character_name}. If left empty, {user_character_name} will decide themselves", # Corrected placeholder f-string parenthesis
                disabled=st.session_state.inner_thought_submitted
            )
            submitted = st.form_submit_button(
                "Share Your Deduction!",
                type="primary",
                use_container_width=True,
                disabled=st.session_state.inner_thought_submitted
            )

            if submitted:
                st.session_state.inner_thought_submitted = True
                st.rerun() # Rerun immediately after submit to hide the form
            elif not st.session_state.inner_thought_submitted:
                # --- If the form was NOT submitted AND we are not in the processing phase, break the loop ---
                break # Exit the while True loop and wait for user interaction

        # --- If form is submitted, continue to processing --- #
        # --- This code now runs *outside the form* after a rerun if the form was submitted ---
        if st.session_state.inner_thought_submitted:
            user_trial_input = st.session_state.get("trial_thought_input") # Get value from form state
            st.session_state.user_input = user_trial_input # Store for the flow

            # Run DecisionNode for the user, incorporating their input
            decision_flow = create_character_decision_flow()
            decision_flow.set_params({'character_name': user_character_name})
            # Pass the entire session state, node's prep will read user_input
            asyncio.run(decision_flow.run_async(st.session_state))

            # Retrieve the AI-generated statement (which considered user input)
            cursor.execute(
                """SELECT content, emotion FROM actions
                    WHERE actor_name = ? AND day = ? AND phase = ? AND action_type = 'statement'
                    ORDER BY id DESC LIMIT 1""",
                (user_character_name, current_day, "CLASS_TRIAL_DISCUSSION") # Use the correct phase
            )
            result = cursor.fetchone()
            user_statement = result[0] if result and result[0] else f"*({user_character_name} says nothing...)*"
            user_emotion = result[1] if result and result[1] else "normal" # Default emotion for trial

            # --- MODIFIED: Get speaker index/total and format message ---
            current_speaker_index = st.session_state.get("user_speaker_index", 0) # Get index stored during transition
            total_speakers_this_trial = st.session_state.get("total_speakers_this_trial", 0)
            full_user_statement = f"({current_speaker_index}/{total_speakers_this_trial}) **{user_character_name}:** {user_statement}"
            # --- END MODIFIED ---

            # Display the user's AI-generated statement
            display_interactive_message(
                character_name=user_character_name,
                content=full_user_statement,
                emotion=user_emotion,
                sleep_time=3
            )

            # Cleanup and transition back
            st.session_state.user_input = None # Clear the user input
            st.session_state.inner_thought_submitted = False # Reset button state for next time
            st.session_state.pop("user_speaker_index", None) # Remove temp index
            st.session_state.current_state = "CLASS_TRIAL_DISCUSSION"
            # NO rerun here, let the main loop continue and re-enter CLASS_TRIAL_DISCUSSION

    # --- Class Trial: Discussion ---
    if st.session_state.current_state == "CLASS_TRIAL_DISCUSSION":
        current_day = st.session_state.current_day
        current_phase = st.session_state.current_state
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        speaking_order_list = st.session_state.shuffled_character_order
        user_character_name = st.session_state.user_character_name
        # Get viewer mode selection directly (though not used for display logic here, might be needed later)
        viewer_mode_selection = st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION)

        # --- Initialization --- (Runs only if current_phase_actors is None)
        if st.session_state.current_phase_actors is None:
            cursor.execute("SELECT name FROM roles WHERE is_alive = 1")
            living_characters_set = {row[0] for row in cursor.fetchall()}
            # Create the ordered list of actors for this phase
            st.session_state.current_phase_actors = [name for name in speaking_order_list if name in living_characters_set]
            # --- ADDED: Store total number of speakers for this trial ---
            st.session_state.total_speakers_this_trial = len(st.session_state.current_phase_actors)
            # --- END ADDED ---

            if not st.session_state.current_phase_actors:
                print(f"Warning: No living players found for Class Trial on Day {current_day}. Skipping.")
                st.session_state.current_phase_actors = None # Ensure reset
                # --- ADDED: Reset total speakers ---
                st.session_state.total_speakers_this_trial = None # Reset
                # --- END ADDED ---
                st.session_state.current_state = "CLASS_TRIAL_VOTE"
                # No rerun here, continue to the next state check in the main loop
            else:
                # Display Speaking Order Announcement (once per trial - always public)
                order_text = " → ".join([f"({i+1}) {name}" for i, name in enumerate(st.session_state.current_phase_actors)])
                speaking_order_message = (
                    f'"*Puhuhu... Now listen up! This here is the **Speaking Order** for *this* Class Trial! Pay attention!*"\n'
                    f' * **🌅 Early speakers** get to set the stage, frame the debate... but they have the *least* info to work with!\n'
                    f' * **🌓 Middle speakers**? They\\\'re the pivot point! They can swing the whole discussion!\n'
                    f' * **🌇 Late speakers**... ah, the pressure! They hear everything, but everyone\\\'s waiting for *their* big finale! Their words carry extra weight right before the vote! Got it?\n\n'
                    f'**Here\\\'s the lineup for today:** {order_text}' # Updated text
                )
                full_speaking_order_message = f'**Monokuma:** {speaking_order_message}'
                display_interactive_message(
                    character_name="Monokuma", content=full_speaking_order_message, emotion="normal", sleep_time=5, audio_path="./assets/announcement.wav"
                )

                # Monokuma introduces the discussion (always public)
                first_living_speaker = st.session_state.current_phase_actors[0] if st.session_state.current_phase_actors else "no one"
                monokuma_intro_speech = f'**Monokuma:** ⚖️ *"Alright, kiddies, let the Class Trial BEGIN! Remember the speaking order! Stick to it, or else! First up is {first_living_speaker}!"*'
                display_interactive_message(
                    character_name="Monokuma", content=monokuma_intro_speech, emotion="blackened", sleep_time=0.5, audio_path="./assets/trial.wav"
                )
                # No rerun here, proceed directly to processing loop below

        # --- Processing Logic --- (Runs only if current_phase_actors is a list)
        if isinstance(st.session_state.current_phase_actors, list):
            # --- ADDED: Get total speakers from session state ---
            total_speakers_this_trial = st.session_state.get("total_speakers_this_trial", 0) # Get total, default 0 if somehow not set
            # --- END ADDED ---
            transitioned_to_input = False # Flag to check if we broke for user input

            while st.session_state.current_phase_actors: # Loop while actors remain
                # --- MODIFIED: Pop first ---
                current_actor = st.session_state.current_phase_actors.pop(0) # Pop the current actor
                # --- END MODIFIED ---

                # --- ADDED: Calculate current speaker index (1-based) ---
                # Index = Total - Remaining_Count (after popping the current actor)
                current_speaker_index = total_speakers_this_trial - len(st.session_state.current_phase_actors)
                # --- END ADDED ---

                # --- MODIFIED: User Input Handling (check after pop) ---
                if current_actor == st.session_state.user_character_name and viewer_mode_selection == PLAYER_MODE_OPTION:
                    # It's the user's turn in Player Mode
                    st.session_state.user_speaker_index = current_speaker_index # Store the calculated index
                    st.session_state.current_state = "CLASS_TRIAL_USER_INPUT"
                    transitioned_to_input = True # Set flag
                    break # Exit the while loop to handle state change

                # --- If not user input, process AI ---

                # Generate Speaker's statement using the Flow (Node acts based on its own perspective)
                decision_flow = create_character_decision_flow()
                decision_flow.set_params({'character_name': current_actor})
                asyncio.run(decision_flow.run_async(st.session_state))

                # Retrieve the statement from the database (always logged by node)
                cursor.execute(
                    """SELECT content, emotion FROM actions
                        WHERE actor_name = ? AND day = ? AND phase = ? AND action_type = 'statement'
                        ORDER BY id DESC LIMIT 1""",
                    (current_actor, current_day, current_phase)
                )
                result = cursor.fetchone()
                speaker_speech = result[0] if result and result[0] else f"*({current_actor} remains silent...)*"
                speaker_emotion = result[1] if result and result[1] else "normal"

                # --- MODIFIED: Display the message with index/total ---
                full_speaker_message = f"({current_speaker_index}/{total_speakers_this_trial}) **{current_actor}:** {speaker_speech}"
                # --- END MODIFIED ---
                display_interactive_message(
                    character_name=current_actor,
                    content=full_speaker_message,
                    emotion=speaker_emotion,
                    sleep_time=0.5
                )
                # No rerun inside the loop

            time.sleep(8)

            # --- After the loop --- #
            # If we broke for user input, do nothing here; the main loop will handle the state change.
            # If the loop finished naturally, transition to the vote phase.
            if transitioned_to_input:
                st.rerun() # Rerun to enter the user input state
            if not transitioned_to_input and not st.session_state.current_phase_actors:
                st.session_state.current_phase_actors = None # Reset for next day/phase
                # --- ADDED: Reset total speakers ---
                st.session_state.total_speakers_this_trial = None # Reset
                # --- END ADDED --

                # End of Discussion message (always public)
                monokuma_end_speech = f'**Monokuma:** *"Phew! That\'s everyone! Now that you\'ve all had your say, it\'s time for the moment of truth... VOTING TIME! Choose wisely, puhuhu!"*'
                display_interactive_message(
                    character_name="Monokuma", content=monokuma_end_speech, emotion="normal", sleep_time=0.5, audio_path="./assets/vote.wav"
                )

                st.session_state.current_state = "CLASS_TRIAL_VOTE"
                # No rerun needed here, main loop continues to the vote block

    # --- Class Trial: Vote --- (MODIFIED for User Input)
    if st.session_state.current_state == "CLASS_TRIAL_VOTE":
        current_day = st.session_state.current_day
        current_phase = st.session_state.current_state
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name
        viewer_mode_selection = st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION)

        # Get list of currently alive characters who will vote
        cursor.execute("SELECT name FROM roles WHERE is_alive = 1 ORDER BY id")
        living_voters = [row[0] for row in cursor.fetchall()] # Get a list of names

        # Check if user needs to vote
        user_is_voter = (user_character_name in living_voters)
        user_needs_to_vote = (user_is_voter and viewer_mode_selection == PLAYER_MODE_OPTION)

        next_state = None

        if user_needs_to_vote:
            # --- User Votes Scenario ---
            ai_voters = [name for name in living_voters if name != user_character_name]
            # Run parallel flow for AI voters if any exist
            if ai_voters:
                st.session_state["acting_characters"] = ai_voters
                parallel_trial_vote_flow = create_parallel_decision_flow()
                # Run async flow
                asyncio.run(parallel_trial_vote_flow.run_async(st.session_state))
                st.session_state.pop("acting_characters", None) # Clean up
            # Transition to user input state
            st.session_state.current_state = "CLASS_TRIAL_VOTE_USER_INPUT"
            st.rerun()

        else:
            # --- AI Votes Only Scenario (Viewer Mode) ---
            # Run parallel flow for ALL voters if any exist
            if living_voters:
                st.session_state["acting_characters"] = living_voters
                parallel_trial_vote_flow = create_parallel_decision_flow()
                # Run async flow
                asyncio.run(parallel_trial_vote_flow.run_async(st.session_state))
                st.session_state.pop("acting_characters", None) # Clean up
            # Transition directly to reveal state
            st.session_state.current_state = "EXECUTION_REVEAL"
            # No rerun needed, main loop continues


    # --- Class Trial: Vote User Input --- (NEW STATE)
    if st.session_state.current_state == "CLASS_TRIAL_VOTE_USER_INPUT":
        current_day = st.session_state.current_day
        current_phase = st.session_state.current_state # Log user vote here
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        user_character_name = st.session_state.user_character_name

        # Reset the submit button state flag
        if 'ct_vote_form_submitted' not in st.session_state:
            st.session_state.ct_vote_form_submitted = False

        # Get living players for voting options
        cursor.execute("SELECT name FROM roles WHERE is_alive = 1 ORDER BY id")
        living_player_names = [row[0] for row in cursor.fetchall()]
        vote_options = ["Abstain"] + living_player_names

        st.info(f"🗳️ **Judgment time, {user_character_name}!** Who is the Blackened? Point the finger!")

        with st.form(key="class_trial_vote_form"):
            selected_choice = st.selectbox(
                "Select player to vote for (or Abstain):",
                options=vote_options,
                key="ct_vote_choice",
                index=0, # Default to Abstain
                disabled=st.session_state.ct_vote_form_submitted
            )
            vote_submitted_button = st.form_submit_button(
                "Confirm Vote",
                type="primary",
                use_container_width=True,
                disabled=st.session_state.ct_vote_form_submitted
            )

            if vote_submitted_button:
                # Set flag immediately and rerun
                st.session_state.ct_vote_form_submitted = True
                st.rerun()
            elif not st.session_state.ct_vote_form_submitted:
                # Wait for user submission
                 break

        # --- Process the vote *after* form submission and rerun --- #
        if st.session_state.ct_vote_form_submitted:
            # Retrieve selected choice
            user_vote_choice = st.session_state.get("ct_vote_choice", "Abstain")

            # Determine target_name (None if Abstain)
            target_name = None if user_vote_choice == "Abstain" else user_vote_choice

            # Log the user's vote using the MAIN phase name
            cursor.execute(
                """INSERT INTO actions (day, phase, actor_name, action_type, target_name)
                   VALUES (?, ?, ?, ?, ?) """,
                (current_day, 'CLASS_TRIAL_VOTE', user_character_name, 'vote', target_name) # Use main phase
            )
            conn.commit()

            # Clean up
            st.session_state.ct_vote_form_submitted = False
            st.session_state.pop("ct_vote_choice", None)

            # Transition to reveal state
            st.session_state.current_state = "EXECUTION_REVEAL"
            # NO rerun needed, main loop continues

    # --- Execution Reveal --- (MODIFIED for User Input Consolidation)
    if st.session_state.current_state == "EXECUTION_REVEAL":
        current_day = st.session_state.current_day
        # Votes could have come from VOTE or VOTE_USER_INPUT phases
        possible_vote_phases = ['CLASS_TRIAL_VOTE', 'CLASS_TRIAL_VOTE_USER_INPUT']
        execution_phase = st.session_state.current_state # Log execution in this phase
        conn = st.session_state.db_conn
        cursor = conn.cursor()

        # --- Retrieve the Final Voted Player --- (Consolidate votes from relevant phases)
        vote_query = """SELECT actor_name, target_name FROM actions
                       WHERE day = ? AND phase = ? AND action_type = 'vote'
                       ORDER BY actor_name """
        cursor.execute(vote_query, (current_day, 'CLASS_TRIAL_VOTE')) # Query only the main phase
        individual_class_votes = cursor.fetchall()

        # Tally votes (use 'none' for tie-breaking in class trial)
        player_to_expel = tally_votes(individual_class_votes, tie_break_strategy='none')

        # Log the final decision (even if None)
        cursor.execute(
            """INSERT INTO actions (day, phase, actor_name, action_type, target_name)
            VALUES (?, ?, ?, ?, ?)""",
            (current_day, execution_phase, "Monokuma", "class_trial_vote_final", player_to_expel)
        )
        conn.commit()

        # --- Display Monokuma Vote Summary ---
        vote_list_str = ""
        if individual_class_votes:
            vote_list_str = format_vote_summary(individual_class_votes)
            # Log the summary only if there were votes
            cursor.execute(
                """INSERT INTO actions (day, phase, actor_name, action_type, content)
                 VALUES (?, ?, ?, ?, ?)""",
                (current_day, execution_phase, "Monokuma", "class_trial_vote_summary", vote_list_str)
            )
            conn.commit()

        # Determine message, audio, and emotion based on outcome
        monokuma_vote_summary_content = None
        monokuma_audio_path = None
        monokuma_emotion = "think" # Default emotion

        if player_to_expel:
            monokuma_vote_summary_content = f'"The votes are in! Tallying, tallying... And the one chosen is... **{player_to_expel}**!"'
            monokuma_audio_path = "./assets/vote_finish.wav"
            monokuma_emotion = "think"
        else: # Only if votes existed but no one was expelled (tie/abstain)
            # Updated angry dialogue
            monokuma_vote_summary_content = f'💢 "Are you stupid or something?! Voting time, but no decision made?! I can\'t stand people having a good time! No execution means NO FUN!"'
            monokuma_audio_path = "./assets/stupid.wav"
            monokuma_emotion = "determined"

        # Display the message if content was set (i.e., one of the conditions above was met)
        if monokuma_vote_summary_content and monokuma_audio_path:
            full_monokuma_vote_summary = f'**Monokuma:** {monokuma_vote_summary_content}{vote_list_str}'
            display_interactive_message(
                character_name="Monokuma",
                content=full_monokuma_vote_summary,
                emotion=monokuma_emotion,
                sleep_time=10,
                audio_path=monokuma_audio_path
            )

        # --- Resolve Execution and Reveal Role ---
        player_role = "Unknown"
        execution_occurred = False # Reset flag

        if player_to_expel:
            cursor.execute("SELECT role FROM roles WHERE name = ?", (player_to_expel,))
            role_result = cursor.fetchone()
            if role_result: # Only proceed if player was found
                player_role = role_result[0]
                execution_occurred = True # Execution will happen

                cursor.execute("UPDATE roles SET is_alive = ? WHERE name = ?", (False, player_to_expel))
                conn.commit()

                # Log execution
                log_content = f"Executed: {player_to_expel}, Role: {player_role}"
                cursor.execute(
                    """INSERT INTO actions (day, phase, actor_name, action_type, target_name, content)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (current_day, execution_phase, "Monokuma", "execution", player_to_expel, log_content)
                )
                conn.commit()

                # Display message FROM the executed player, stating execution and role
                execution_message_content = f'...**{player_to_expel}** has been executed! Turns out, they were the **{player_role}**!...'
                display_interactive_message(
                    character_name=player_to_expel, # Attributed to the executed player
                    content=execution_message_content,
                    emotion="death",
                    sleep_time=8, # Longer pause for impact
                    audio_path="./assets/execution.wav"
                )

                # --- Player Execution Check (Moved After Message) ---
                if player_to_expel == st.session_state.user_character_name and st.session_state.get("viewer_mode_selection", PLAYER_MODE_OPTION) == PLAYER_MODE_OPTION:
                    st.error("⚖️ **You have been executed!** Though your journey ends here, the trial isn't over! You can keep watching to see how things unfold for the remaining participants.")
                # --- End Player Execution Check ---
            # else: If role_result is None (player not found), do nothing here. The outer else handles no expulsion.

        if not execution_occurred: # Covers both no one chosen and player wasn't found
            # Log no execution (if player_to_expel was None initially)
            if player_to_expel is None:
                cursor.execute(
                    """INSERT INTO actions (day, phase, actor_name, action_type)
                    VALUES (?, ?, ?, ?)""",
                    (current_day, execution_phase, "Monokuma", "no_execution")
                )
                conn.commit()
            # No specific log needed if player existed but role didn't (rare case)

        # --- Increment Day and Check Win Conditions --- (Happens AFTER execution/no execution)
        st.session_state.current_day += 1
        cursor.execute("SELECT COUNT(*) FROM roles WHERE role = 'Blackened' AND is_alive = 1")
        living_blackened_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM roles WHERE role != 'Blackened' AND is_alive = 1")
        living_others_count = cursor.fetchone()[0]

        next_state = None
        if living_blackened_count == 0:
            next_state = "GAME_OVER_HOPE"
        elif living_blackened_count >= living_others_count:
            next_state = "GAME_OVER_DESPAIR"
        else:
            st.session_state.current_phase_actors = None # Reset actors list for next phase
            next_state = "NIGHT_PHASE_BLACKENED_DISCUSSION"

        st.session_state.current_state = next_state

    # --- Game Over: Hope Wins ---
    if st.session_state.current_state == "GAME_OVER_HOPE":
        st.balloons()
        st.success("GAME OVER: HOPE WINS!")
        display_interactive_message(
            character_name="Monokuma",
            content='**Monokuma:** *"Nooooo! How could this happen?! You wretched symbols of hope... you actually did it! You found all the Blackened! Ugh, fine, you win... this time! Enjoy your... whatever it is you hope for."*',
            emotion="determined",
            sleep_time=0.5,
            audio_path="./assets/notover.wav"
        )
        st.stop() # End the app execution

    # --- Game Over: Despair Wins ---
    if st.session_state.current_state == "GAME_OVER_DESPAIR":
        st.error("GAME OVER: DESPAIR WINS!")
        conn = st.session_state.db_conn
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM roles WHERE role = 'Blackened'")
        living_blackened = [row[0] for row in cursor.fetchall()]
        blackened_names_str = ", ".join(living_blackened) if living_blackened else "no one... wait, that can't be right?"

        final_message = f'**Monokuma:** *"Puhuhuhu! YES! YES! Despair triumphs! The Blackened have overwhelmed the hopefuls! Such beautiful, delicious despair! And your triumphant Blackened are: **{blackened_names_str}**! Now, let\'s get to the Punishment Time for the losers!"*'

        display_interactive_message(
            character_name="Monokuma",
            content=final_message, # Use the updated message
            emotion="blackened",
            sleep_time=0.5
        )
        st.stop() # End the app execution

    print("DEBUG: current_state", st.session_state.current_state)
    # wait for 1 second
    time.sleep(1)
