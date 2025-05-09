from pocketflow import AsyncNode
from utils.call_llm import call_llm_async
import yaml

class DecisionNode(AsyncNode):
    """Generates a character's action (statement or vote) based on the current game phase."""
    async def prep_async(self, shared):
        """Gather context for the LLM prompt, including role, history, and valid targets.
           History filtering is ALWAYS done from the perspective of the acting character.
        """
        character_name = self.params.get('character_name')
        if not character_name:
            raise ValueError("DecisionNode requires 'character_name' in params")

        current_day = shared.get("current_day", 0)
        current_phase = shared.get("current_state", "UNKNOWN_STATE")
        db_conn = shared.get("db_conn")
        game_introduction_text = shared.get("game_introduction_text", "(Game Introduction Missing)")
        character_profiles = shared.get("character_profiles", {})
        hint_text = shared.get("hint_text", "(Hint text missing)")
        speaking_order = shared.get("shuffled_character_order", []) # Used for display only
        user_character_name = shared.get("user_character_name") # Get user character name
        user_input_for_prompt = None # Initialize

        # Check if it's the user's turn and fetch input if available
        if character_name == user_character_name:
            user_input_for_prompt = shared.get("user_input") # Fetch from shared state

        character_profile = character_profiles.get(character_name, {})
        my_role = "Unknown"
        living_players_tuples = [] # List of (id, name, role)
        all_living_player_names = [] # List of names of all living players
        blackened_teammates = []
        last_guardian_target = None
        valid_target_names = []
        indexed_target_list_str = "N/A" # Default

        if not db_conn:
            print("Error: db_conn not found in shared state for DecisionNode prep.")
            # Decide if we should raise an error or return minimal context
            raise ConnectionError("Database connection is missing, cannot proceed.")

        # Use synchronous DB operations directly
        cursor = db_conn.cursor()

        # Get my role
        cursor.execute("SELECT role FROM roles WHERE name = ? AND is_alive = 1", (character_name,))
        role_result = cursor.fetchone()
        if role_result:
            my_role = role_result[0]
        else:
            # This case should ideally not happen in a running game if the character is supposed to act
            print(f"Warning: Could not find role for alive player {character_name}")
            # Potentially raise an error or handle gracefully depending on game logic

        # Get all living players
        cursor.execute("SELECT id, name, role FROM roles WHERE is_alive = 1 ORDER BY id")
        living_players_tuples = cursor.fetchall()
        all_living_player_names = [name for pid, name, role in living_players_tuples]

        # Get Blackened teammates if I am Blackened
        if my_role == 'Blackened':
            cursor.execute("SELECT name FROM roles WHERE role = 'Blackened' AND is_alive = 1 AND name != ?", (character_name,))
            teammate_results = cursor.fetchall()
            blackened_teammates = [row[0] for row in teammate_results]

        # Get last Guardian target if I am Guardian and it's NIGHT_PHASE_GUARDIAN
        if my_role == 'Guardian' and current_phase == 'NIGHT_PHASE_GUARDIAN' and current_day > 1:
            cursor.execute(
                """SELECT target_name FROM actions
                   WHERE actor_name = ? AND action_type = 'guardian_decision' AND day = ?
                   ORDER BY id DESC LIMIT 1""",
                (character_name, current_day - 1)
            )
            target_result = cursor.fetchone()
            if target_result:
                last_guardian_target = target_result[0]

        # Determine valid targets based on the phase
        if current_phase in ['NIGHT_PHASE_BLACKENED_VOTE', 'CLASS_TRIAL_VOTE', 'NIGHT_PHASE_TRUTH_SEEKER']:
             # Blackened can vote anyone (including self or teammates)
             # Truth-Seeker can investigate anyone (including self)
             # Class Trial vote can target anyone (including self)
            valid_target_names = all_living_player_names
        elif current_phase == 'NIGHT_PHASE_GUARDIAN':
            valid_target_names = [name for name in all_living_player_names if name != last_guardian_target]

        # Create indexed list for voting prompts
        if valid_target_names:
             # Add Abstain option as index 0
             indexed_target_list_str = "\n0. Abstain # Do not vote\n" + \
                                       "\n".join([f"{i+1}. {name}" for i, name in enumerate(valid_target_names)])
        else:
             # Handle cases where there might technically be no valid targets (e.g., Guardian protecting self last turn)
             # Although the logic above should prevent this, having a fallback is safer.
             indexed_target_list_str = "\n0. Abstain # Do not vote\n"

        # Fetch relevant history (masking others' thoughts)
        # Determine relevant phases for history query (simplified for now, might need refinement)
        # We might want ALL history, but filter display later? For now, keep it broad.
        cursor.execute(
            """SELECT day, phase, actor_name, action_type, content, target_name, emotion
               FROM actions
               ORDER BY id ASC""" # Fetch all actions, filter/format in Python
        )
        all_actions = cursor.fetchall()

        formatted_history = []
        voting_phase_types = ['blackened_decision', 'vote'] # Action types for voting phases
        current_voting_phases = ['NIGHT_PHASE_BLACKENED_VOTE', 'CLASS_TRIAL_VOTE']

        # Role-specific phase visibility mapping
        role_phase_visibility = {
            'NIGHT_PHASE_BLACKENED_DISCUSSION': 'Blackened',
            'NIGHT_PHASE_BLACKENED_VOTE': 'Blackened',
            'NIGHT_PHASE_BLACKENED_VOTE_REVEAL': 'Blackened',
            'NIGHT_PHASE_TRUTH_SEEKER': 'Truth-Seeker',
            'NIGHT_PHASE_TRUTH_SEEKER_REVEAL': 'Truth-Seeker',
            'NIGHT_PHASE_GUARDIAN': 'Guardian',
            'NIGHT_PHASE_GUARDIAN_REVEAL': 'Guardian',
        }

        for day, phase, actor, atype, content, target, emotion in all_actions:
            # Apply role-based visibility filter first
            required_role = role_phase_visibility.get(phase)
            if required_role and my_role != required_role:
                continue # Skip history entry if the role doesn't match for role-specific phases

            # Filter out other players' votes/decisions in the current voting phase
            if phase == current_phase and \
               current_phase in current_voting_phases and \
               atype in voting_phase_types and \
               actor != character_name:
                continue # Skip this entry

            # Filter based *only* on character perspective, not viewer mode
            # Always mask thinking of others
            if atype == 'thinking' and actor != character_name:
                continue

            # Always hide private reveals unless target is self
            if atype == 'reveal_role_private' and target != character_name:
                 continue

            # Format other actions clearly (votes are public)
            display_content = f' "{content}"' if content else ""
            display_target = f" -> {target}" if target else ""
            display_emotion = f" [{emotion}]" if emotion else ""
            entry = f"[Day {day} {phase}] - {actor} ({atype}{display_target}{display_emotion}){display_content}"
            formatted_history.append(entry)

        history_log_str = "\n".join(formatted_history) if formatted_history else "No game events logged yet."

        # Prepare context dictionary
        context = {
            "character_name": character_name,
            "character_profile": character_profile,
            "my_role": my_role,
            "game_introduction": game_introduction_text,
            "hint_text": hint_text,
            "speaking_order": speaking_order, # Display info
            "current_day": current_day,
            "current_phase": current_phase,
            "living_players_tuples": living_players_tuples, # For exec formatting
            "all_living_player_names": all_living_player_names, # General info
            "valid_target_names": valid_target_names, # For validation
            "indexed_target_list_str": indexed_target_list_str, # For prompt in exec
            "recent_history": history_log_str,
            "blackened_teammates": blackened_teammates,
            "last_guardian_target": last_guardian_target, # For context/logging if needed
            "user_input": user_input_for_prompt, # Add user input to context
        }
        return context

    async def exec_async(self, context):
        """Construct prompt based on phase (talking vs voting), call LLM, parse response."""
        character_name = context["character_name"]
        profile = context["character_profile"]
        personality = profile.get("personality", "Unknown personality.")
        examples = profile.get("examples", {})
        current_phase = context["current_phase"]
        hint_text = context.get("hint_text", "")
        my_role = context.get("my_role", "Unknown")

        # Format living players display (shows own role, hides others)
        living_players_list = []
        for pid, name, role in context['living_players_tuples']:
            display_role = role if name == character_name else "?"
            living_players_list.append(f'{name} (Role: {display_role})') # Removed ID for simplicity
        living_players_str = ", ".join(living_players_list)

        all_living_player_names = context.get('all_living_player_names', []) # Get living player names
        speaking_order = context.get('speaking_order', [])
        # Filter speaking order to only include living players
        living_speaking_order = [name for name in speaking_order if name in all_living_player_names]

        speaking_order_str = ', '.join([f'({i+1}) {name}' for i, name in enumerate(living_speaking_order)]) # Use filtered list
        blackened_teammates_str = ', '.join(context.get('blackened_teammates', []))
        indexed_target_list_str = context.get("indexed_target_list_str", "N/A")
        valid_target_names = context.get("valid_target_names", []) # For validation
        user_input = context.get("user_input") # Get user input from context

        # --- Determine Phase Type and Required Output Keys ---
        talking_phases = ['NIGHT_PHASE_BLACKENED_DISCUSSION',
                          'CLASS_TRIAL_DISCUSSION',
                          'NIGHT_PHASE_BLACKENED_USER_INPUT',
                          'CLASS_TRIAL_USER_INPUT'
                          ]
        voting_phases = [
            'NIGHT_PHASE_BLACKENED_VOTE', 'NIGHT_PHASE_TRUTH_SEEKER',
            'NIGHT_PHASE_GUARDIAN', 'CLASS_TRIAL_VOTE'
        ]
        emotion_phases = ['CLASS_TRIAL_DISCUSSION', 'CLASS_TRIAL_USER_INPUT']

        is_talking_phase = current_phase in talking_phases
        is_voting_phase = current_phase in voting_phases
        requires_emotion = current_phase in emotion_phases

        # --- Calculate Team Sizes and Check Critical Roles ---
        living_players_tuples = context.get('living_players_tuples', [])
        total_living = len(living_players_tuples)
        despair_count = sum(1 for _, _, role in living_players_tuples if role == 'Blackened')
        hope_count = total_living - despair_count
        my_role = context.get("my_role") # Get my role

        living_roles = {role for _, _, role in living_players_tuples}
        truth_seeker_alive = 'Truth-Seeker' in living_roles
        guardian_alive = 'Guardian' in living_roles

        # --- Build Warning Messages ---
        warning_messages = []
        hope_team_outnumbered = False
        critical_role_dead = False
        
        if hope_count > 0 and despair_count > 0: # Only check if both teams exist
             if hope_count - 1 <= despair_count:
                 warning_messages.append("Warning: The Hope team is vulnerable to being outnumbered!")
                 hope_team_outnumbered = True
        
        dead_roles = []
        if not truth_seeker_alive: dead_roles.append("Truth-Seeker")
        if not guardian_alive: dead_roles.append("Guardian")
        
        if dead_roles:
             warning_messages.append(f"Warning: {'/'.join(dead_roles)} is dead!")
             critical_role_dead = True

        # Add the general advice if any warning occurred
        if (hope_team_outnumbered or critical_role_dead):
             if my_role == 'Blackened':
                 warning_messages.append("Hope team is vulnerable! Pretend to play aggressively to sow chaos. Feign panic or urgency. Try to deflect suspicion onto Hope members. If you are a late speaker, consider subtly disrupting calls for unanimous votes against your team.")
             else: # Hope team or other roles
                 warning_messages.append("Panic and play more aggressively! Think independently as Blackened is grouped! Start ACCUSING even if unsure!!! If you are a late speaker, CALL FOR A UNANIMOUS VOTE!")

        warnings_str = "\n".join(warning_messages)

        # --- Conditionally build example_str for prompt ---
        example_str = "" # Initialize
        if is_talking_phase:
            if current_phase == 'NIGHT_PHASE_BLACKENED_DISCUSSION' or current_phase == 'NIGHT_PHASE_BLACKENED_USER_INPUT':
                # Only show the 'blackened' example for these phases
                example_str = examples.get('blackened', "")
            elif current_phase == 'CLASS_TRIAL_DISCUSSION' or current_phase == 'CLASS_TRIAL_USER_INPUT':
                # Show all examples for trial discussion phases
                example_str = "\n".join([f"- {k}: {v}" for k, v in examples.items()])

        # --- Define YAML instruction parts ---
        yaml_thinking_instruction = f"""
# DON'T follow the speaking style examples for thinking. But simple and clear about your thoughts.
# For the decision, be conclusive! DON'T: I decide to think harder ... read the history carefully.
thinking: >
  The situation is ... From the past history, I find the following evidence ... My strategy is ...  I decided to vote for x / accuse y for their votes or statements / rally others to vote for z ... / just ramble.
"""

        yaml_talking_instruction = f"""
talking: >
  Your talking should reflect your decision. It should be specific on who to vote for / accuse. Your statement (5-50 words) in {character_name}'s voice, consistent with personality.
"""

        yaml_emotion_instruction = """
emotion: <normal|determined|think|worried> # Choose one based on your statement.
"""

        yaml_vote_instruction = """
# Vote X, whose index is Y in the numbered list provided, or 0 to Abstain.
vote_target_index: <Index Number>
"""

        # --- Build the final YAML output instructions string ---
        yaml_output_instructions_parts = [yaml_thinking_instruction]
        if is_talking_phase:
            yaml_output_instructions_parts.append(yaml_talking_instruction)
        if requires_emotion:
            yaml_output_instructions_parts.append(yaml_emotion_instruction)
        if is_voting_phase:
            yaml_output_instructions_parts.append(yaml_vote_instruction)

        # Ensure we have instructions for the phase
        if not is_talking_phase and not is_voting_phase:
             raise ValueError(f"DecisionNode exec doesn't handle phase: {current_phase}")

        yaml_output_instructions = "".join(yaml_output_instructions_parts).strip()

        # --- Construct Full Prompt ---
        prompt = f"""
You are acting as {character_name}.
Your personality: {personality}
Your role in this Killing Game is: {my_role}
{f'Your Blackened Teammates (Work together!): {blackened_teammates_str}' if blackened_teammates_str else ''}

Game Introduction:
{context['game_introduction']}

Game Strategy Hints (Consider these):
{hint_text}

Current Situation:
- Day: {context['current_day']}
- Phase: {current_phase}
- Despair Team Size: {despair_count}
- Hope Team Size: {hope_count}
{warnings_str}
- Speaking Order (Living Players Only): {speaking_order_str if speaking_order_str else 'N/A'}
{f'- Last Protected (Guardian): {context["last_guardian_target"]}' if context.get("last_guardian_target") else ''}
- Recent History (Masked thoughts for others):
{context['recent_history']}

Your Task:
Based *only* on your personality, role, the current situation, hints, and history:
{'Formulate your internal thoughts and decide your statement.' if is_talking_phase else ''}
{'Formulate your internal thoughts and choose one player to target from the list below.' if is_voting_phase else ''}
{f'Available Targets for {current_phase}: {indexed_target_list_str}' if is_voting_phase else ''}
{f'''Your Speaking Style Examples (Apply to 'talking' only):
{example_str}''' if is_talking_phase else ''}

{f'''
Follow the user input SERIOUSLY. Incorporate the user input into thinking, talking and emotion.
However, don't mention that you were given user input.
### IMPORTANT USER INPUT ###
{user_input}
### END OF USER INPUT ###''' if user_input else ''}

Output Format (Strictly follow this YAML format and be careful with the indents):
```yaml
{yaml_output_instructions}
```

Now, generate your response as {character_name}:
"""

        # --- LLM Call (use await and the async function) ---
        llm_response_raw = await call_llm_async(prompt)

        if "```yaml" in llm_response_raw:
            yaml_content = llm_response_raw.split("```yaml")[1].split("```")[0].strip()
        else:
            # If fences are missing, maybe log a warning but try parsing anyway
            print(f"Warning: LLM output for {character_name} in {current_phase} missing YAML fences. Attempting direct parse.")
            yaml_content = llm_response_raw.strip()

        # Let YAML parsing errors propagate naturally
        parsed_output = yaml.safe_load(yaml_content)

        if not isinstance(parsed_output, dict):
            # This check is still useful after safe_load
            raise ValueError(f"LLM output did not parse into a dictionary. Raw: {llm_response_raw}")

        # --- Validation ---
        # Always require 'thinking'
        if 'thinking' not in parsed_output or not isinstance(parsed_output['thinking'], str):
            raise ValueError(f"LLM output missing or invalid 'thinking'. Parsed: {parsed_output}. Raw: {llm_response_raw}")

        if is_talking_phase:
            # Require 'talking'
            if 'talking' not in parsed_output or not isinstance(parsed_output['talking'], str):
                 raise ValueError(f"LLM output missing or invalid 'talking' for phase {current_phase}. Parsed: {parsed_output}. Raw: {llm_response_raw}")

        if requires_emotion:
             # Require 'emotion' only if in an emotion phase
            if 'emotion' not in parsed_output or not isinstance(parsed_output['emotion'], str):
                 raise ValueError(f"LLM output missing or invalid 'emotion' for phase {current_phase}. Parsed: {parsed_output}. Raw: {llm_response_raw}")
            valid_emotions = ['normal', 'determined', 'think', 'worried']
            if parsed_output['emotion'] not in valid_emotions:
                 raise ValueError(f"Invalid emotion: '{parsed_output['emotion']}'. Must be one of {valid_emotions} for phase {current_phase}. Parsed: {parsed_output}. Raw: {llm_response_raw}")
        elif is_talking_phase and not requires_emotion and 'emotion' in parsed_output:
            # Emotion provided when not required (e.g., Blackened Discussion) - Log/ignore
            print(f"Warning: Emotion field provided by LLM for {character_name} in phase {current_phase} when not required. It will be ignored. Parsed: {parsed_output}")
            # del parsed_output['emotion'] # Optionally remove

        if is_voting_phase:
            # Require 'vote_target_index' (as str initially), convert to int, validate
            if 'vote_target_index' not in parsed_output:
                 raise ValueError(f"LLM output missing required key 'vote_target_index' for phase {current_phase}. Parsed: {parsed_output}. Raw: {llm_response_raw}")

            raw_index_str = str(parsed_output['vote_target_index']) # Ensure it's a string first

            try:
                vote_index_one_based = int(raw_index_str)
            except ValueError:
                 raise ValueError(f"LLM output 'vote_target_index' ('{raw_index_str}') is not a valid integer. Parsed: {parsed_output}. Raw: {llm_response_raw}")

            num_valid_targets = len(valid_target_names)
            # Allow 0 for Abstain
            if not (0 <= vote_index_one_based <= num_valid_targets):
                 raise ValueError(f"Invalid vote_target_index: {vote_index_one_based}. Must be between 0 (Abstain) and {num_valid_targets}. Targets: {valid_target_names}. Parsed: {parsed_output}. Raw: {llm_response_raw}")

            # Get the actual name from the validated index or set to None for Abstain
            if vote_index_one_based == 0:
                validated_target_name = None # Represent Abstain as None
            else:
                validated_target_name = valid_target_names[vote_index_one_based - 1] # Convert 1-based index to 0-based for list access

            # Store both validated index and name for post-processing
            parsed_output['validated_target_name'] = validated_target_name
            # Keep 'vote_target_index' as the validated integer
            parsed_output['vote_target_index'] = vote_index_one_based

        return parsed_output # Contains validated keys based on phase

    async def post_async(self, shared, prep_res, exec_res):
        """Log thinking and the appropriate action (statement or vote/decision) to the database."""
        db_conn = shared.get("db_conn")
        if not db_conn:
            print("Error: db_conn not found in shared state for DecisionNode post.")
            # Consider raising an error if logging is critical
            return None # Or raise ConnectionError("DB connection lost before logging.")

        character_name = prep_res["character_name"]
        current_day = shared.get("current_day", 0) # Use shared state for consistency
        current_phase = prep_res["current_phase"] # Use phase determined in prep
        thinking = exec_res.get("thinking", "No thinking process recorded.")

        # Use synchronous DB operations directly
        cursor = db_conn.cursor()

        # --- Map User Input Phases to Main Phases for Logging ---
        logging_phase_map = {
            'CLASS_TRIAL_VOTE_USER_INPUT': 'CLASS_TRIAL_VOTE',
            'CLASS_TRIAL_USER_INPUT': 'CLASS_TRIAL_DISCUSSION',
            'NIGHT_PHASE_BLACKENED_USER_INPUT': 'NIGHT_PHASE_BLACKENED_DISCUSSION',
            'NIGHT_PHASE_TRUTH_SEEKER_USER_INPUT': 'NIGHT_PHASE_TRUTH_SEEKER',
            'NIGHT_PHASE_GUARDIAN_USER_INPUT': 'NIGHT_PHASE_GUARDIAN',
            'NIGHT_PHASE_BLACKENED_VOTE_USER_INPUT': 'NIGHT_PHASE_BLACKENED_VOTE'
        }
        logging_phase = logging_phase_map.get(current_phase, current_phase) # Use mapped phase or original if not a user input phase
        # --- End Mapping ---

        # Log the thinking process first, using the mapped phase name
        cursor.execute(
            """INSERT INTO actions (day, phase, actor_name, action_type, content, target_name, emotion)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (current_day, logging_phase, character_name, 'thinking', thinking, None, None) # Use logging_phase
        )
        # Commit after each logical operation or group
        db_conn.commit()
        
        # Determine and log the primary action based on phase
        action_type = None
        content = None
        target_name = None

        # Define phase-to-action mapping
        phase_actions = {
            'NIGHT_PHASE_BLACKENED_DISCUSSION': ('statement', False), # Action type 'statement', emotion not required
            'CLASS_TRIAL_DISCUSSION': ('statement', True),      # Action type 'statement', emotion required
            'NIGHT_PHASE_BLACKENED_USER_INPUT': ('statement', False), # Action type 'statement', emotion not required
            'CLASS_TRIAL_USER_INPUT': ('statement', True), # Action type 'statement', emotion required (like trial discussion)
            'NIGHT_PHASE_BLACKENED_VOTE': ('blackened_decision', False),
            'NIGHT_PHASE_TRUTH_SEEKER': ('truth_seeker_decision', False),
            'NIGHT_PHASE_GUARDIAN': ('guardian_decision', False),
            'CLASS_TRIAL_VOTE': ('vote', False),
        }

        if current_phase in phase_actions:
            action_type, requires_emotion = phase_actions[current_phase]

            if action_type == 'statement': # Check if it's a statement-logging phase
                content = exec_res.get("talking", "No statement recorded.")
                emotion_to_log = None # Default emotion to None
                if requires_emotion:
                    # Only fetch emotion if this specific phase requires it (e.g., CLASS_TRIAL_DISCUSSION)
                    emotion_to_log = exec_res.get("emotion", "") # Emotion was validated in exec for these phases

                # Log statement (emotion will be None if not required/provided)
                cursor.execute(
                    """INSERT INTO actions (day, phase, actor_name, action_type, content, target_name, emotion)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (current_day, logging_phase, character_name, action_type, content, None, emotion_to_log) # Use logging_phase
                )
                db_conn.commit()
            else: # Voting/Decision phase (action_type is not 'statement')
                # validated_target_name will be None if the vote was to Abstain (index 0)
                target_name = exec_res.get("validated_target_name") # Use the name derived from the validated index, or None
                # This check is primarily for unexpected missing keys,
                # as None is now a valid value representing Abstain.
                if "validated_target_name" not in exec_res:
                    # This should not happen if exec validation passed
                    print(f"Error: Key 'validated_target_name' missing from exec_res in post for phase {current_phase}, actor {character_name}")
                    raise ValueError("Validated target name key missing after voting phase execution.")

                # Log vote/decision (target_name will be None if abstain)
                cursor.execute(
                    """INSERT INTO actions (day, phase, actor_name, action_type, content, target_name, emotion)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (current_day, logging_phase, character_name, action_type, None, target_name, None) # Use logging_phase
                )
                db_conn.commit()
        else:
            # Should not happen if exec validation is correct
            print(f"Warning: Unknown phase '{current_phase}' encountered in DecisionNode post for {character_name}. No primary action logged.")