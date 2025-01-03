import asyncio
import random
import textwrap
from datetime import datetime

import discord
from consts import GitHubGPU, ModalGPU
from discord import Interaction, SelectOption, app_commands, ui
from discord.ext import commands
from leaderboard_db import leaderboard_name_autocomplete
from utils import (
    display_lb_submissions,
    extract_score,
    send_discord_message,
    setup_logging,
)

logger = setup_logging()


async def async_submit_github_job(
    interaction: discord.Interaction,
    leaderboard_name: str,
    script: discord.Attachment,
    github_command,
    reference_code,
    bot,
    submission_content,
    github_cog: commands.Cog,
    gpu: str,
):
    try:
        github_thread = await github_command.callback(
            github_cog,
            interaction,
            script,
            app_commands.Choice(
                name=gpu,
                value=GitHubGPU[gpu].value,
            ),
            reference_code=reference_code,
        )
    except discord.errors.NotFound as e:
        print(f"Webhook not found: {e}")
        await send_discord_message(interaction, "❌ The webhook was not found.")

    message_contents = [msg.content async for msg in github_thread.history(limit=None)]

    # Compute eval or submission score, call runner here.
    # TODO: Make this more robust later
    score = extract_score("".join(message_contents))

    with bot.leaderboard_db as db:
        db.create_submission(
            {
                "submission_name": script.filename,
                "submission_time": datetime.now(),
                "leaderboard_name": leaderboard_name,
                "code": submission_content,
                "user_id": interaction.user.id,
                "submission_score": score,
                "gpu_type": gpu,
            }
        )

    user_id = (
        interaction.user.global_name if interaction.user.nick is None else interaction.user.nick
    )

    await send_discord_message(
        interaction,
        f"Successfully ran on {gpu} using GitHub runners!\n"
        + f"Leaderboard '{leaderboard_name}'.\n"
        + f"Submission title: {script.filename}.\n"
        + f"Submission user: {user_id}.\n"
        + f"Runtime: {score:.9f} seconds.",
        ephemeral=True,
    )


class LeaderboardSubmitCog(app_commands.Group):
    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot: commands.Bot = bot

        super().__init__(name="submit", description="Submit leaderboard data")

    # Parent command that defines global options
    @app_commands.describe(
        leaderboard_name="Name of the competition / kernel to optimize",
        script="The Python / CUDA script file to run",
    )
    @app_commands.autocomplete(leaderboard_name=leaderboard_name_autocomplete)
    # TODO: Modularize this so all the write functionality is in here. Haven't figured
    # a good way to do this yet.
    async def submit(
        self,
        interaction: discord.Interaction,
        leaderboard_name: str,
        script: discord.Attachment,
    ):
        pass

    @app_commands.command(name="modal", description="Submit leaderboard data for modal")
    @app_commands.describe(
        gpu_type="Choose the GPU type for Modal",
    )
    @app_commands.autocomplete(leaderboard_name=leaderboard_name_autocomplete)
    @app_commands.choices(
        gpu_type=[app_commands.Choice(name=gpu.value, value=gpu.value) for gpu in ModalGPU]
    )
    async def submit_modal(
        self,
        interaction: discord.Interaction,
        leaderboard_name: str,
        script: discord.Attachment,
        gpu_type: app_commands.Choice[str],
    ):
        try:
            # Read the template file
            submission_content = await script.read()

            # Call Modal runner
            modal_cog = self.bot.get_cog("ModalCog")

            if not all([modal_cog]):
                await send_discord_message(interaction, "❌ Required cogs not found!")
                return

            # Compute eval or submission score, call runner here.
            score = random.random()

            with self.bot.leaderboard_db as db:
                db.create_submission(
                    {
                        "submission_name": script.filename,
                        "submission_time": datetime.now(),
                        "leaderboard_name": leaderboard_name,
                        "code": submission_content,
                        "user_id": interaction.user.id,
                        "submission_score": score,
                        "gpu_type": gpu_type.name,
                    }
                )

            await send_discord_message(
                interaction,
                f"Ran on Modal. Leaderboard '{leaderboard_name}'.\n"
                + f"Submission title: {script.filename}.\n"
                + f"Submission user: {interaction.user.id}.\n"
                + f"Runtime: {score:.9f} seconds.",
                ephemeral=True,
            )
        except ValueError:
            await send_discord_message(
                interaction,
                "Invalid date format. Please use YYYY-MM-DD or YYYY-MM-DD HH:MM",
                ephemeral=True,
            )

    @app_commands.command(name="github", description="Submit leaderboard data for GitHub")
    @app_commands.autocomplete(leaderboard_name=leaderboard_name_autocomplete)
    async def submit_github(
        self,
        interaction: discord.Interaction,
        leaderboard_name: str,
        script: discord.Attachment,
    ):
        # Read the template file
        submission_content = await script.read()

        try:
            submission_content = submission_content.decode()
        except UnicodeError:
            await send_discord_message(
                interaction, "Could not decode your file. Is it UTF-8?", ephemeral=True
            )
            return

        try:
            # Read and convert reference code
            reference_code = None
            with self.bot.leaderboard_db as db:
                # TODO: query that gets reference code given leaderboard name
                leaderboard_item = db.get_leaderboard(leaderboard_name)
                if not leaderboard_item:
                    await send_discord_message(
                        interaction,
                        f"Leaderboard {leaderboard_name} not found.",
                        ephemeral=True,
                    )
                    return
                reference_code = leaderboard_item["reference_code"]
                gpus = db.get_leaderboard_gpu_types(leaderboard_name)

            if not interaction.response.is_done():
                await interaction.response.defer()

            # Call GH runner
            github_cog = self.bot.get_cog("GitHubCog")

            if not all([github_cog]):
                await send_discord_message(interaction, "❌ Required cogs not found!")
                return

            view = GPUSelectionView(gpus, interaction.user)

            await send_discord_message(
                interaction,
                f"Please select GPUs to submit to submit for leaderboard: {leaderboard_name}.",
                view=view,
                ephemeral=True,
            )

            await view.wait()

            github_command = github_cog.run_github

            tasks = [
                async_submit_github_job(
                    interaction,
                    leaderboard_name,
                    script,
                    github_command,
                    reference_code,
                    self.bot,
                    reference_code,
                    github_cog,
                    gpu,
                )
                for gpu in view.selected_gpus
            ]
            await asyncio.gather(*tasks)

        except ValueError:
            await send_discord_message(
                interaction,
                "Invalid date format. Please use YYYY-MM-DD or YYYY-MM-DD HH:MM",
                ephemeral=True,
            )


class GPUSelectionView(ui.View):
    def __init__(self, available_gpus: list[str], original_user: discord.User):
        super().__init__()
        self.original_user = original_user
        self.selected_gpus = None

        # Add the Select Menu with the list of GPU options
        select = ui.Select(
            placeholder="Select GPUs for this leaderboard...",
            options=[SelectOption(label=gpu, value=gpu) for gpu in available_gpus],
            min_values=1,  # Minimum number of selections
            max_values=len(available_gpus),  # Maximum number of selections
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.original_user:
            await interaction.response.send_message(
                f"This selection menu is only for {self.original_user.name}!", ephemeral=True
            )
            return False
        return True

    async def select_callback(self, interaction: Interaction):
        # Retrieve the selected options
        select = interaction.data["values"]
        self.selected_gpus = select
        await send_discord_message(
            interaction, f"Selected GPUs: {', '.join(self.selected_gpus)}", ephemeral=True
        )
        self.stop()


class DeleteConfirmationModal(ui.Modal, title="Confirm Deletion"):
    def __init__(self, leaderboard_name: str, db):
        super().__init__()
        self.leaderboard_name = leaderboard_name
        self.db = db
        self.confirmation = ui.TextInput(
            label=f"Type '{leaderboard_name}' to confirm deletion",
            placeholder="Enter the leaderboard name",
            required=True,
        )
        self.add_item(self.confirmation)

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirmation.value == self.leaderboard_name:
            with self.db as db:
                err = db.delete_leaderboard(self.leaderboard_name)
                if err:
                    await send_discord_message(
                        interaction,
                        "An error occurred while deleting the leaderboard.",
                        ephemeral=True,
                    )
                else:
                    await send_discord_message(
                        interaction,
                        f"Leaderboard '{self.leaderboard_name}' deleted.",
                        ephemeral=True,
                    )
        else:
            await send_discord_message(
                interaction,
                "Deletion cancelled: The leaderboard name didn't match.",
                ephemeral=True,
            )


class LeaderboardCog(commands.Cog):
    def __init__(self, bot):
        self.bot: commands.Bot = bot
        self.get_leaderboards = bot.leaderboard_group.command(name="list")(self.get_leaderboards)
        self.leaderboard_create = bot.leaderboard_group.command(
            name="create", description="Create a new leaderboard"
        )(self.leaderboard_create)

        bot.leaderboard_group.add_command(LeaderboardSubmitCog(bot))

        self.get_leaderboard_submissions = bot.leaderboard_group.command(
            name="show", description="Get all submissions for a leaderboard"
        )(self.get_leaderboard_submissions)

        self.delete_leaderboard = bot.leaderboard_group.command(
            name="delete", description="Delete a leaderboard"
        )(self.delete_leaderboard)

    async def get_leaderboards(self, interaction: discord.Interaction):
        """Display all leaderboards in a table format"""
        await interaction.response.defer()

        with self.bot.leaderboard_db as db:
            leaderboards = db.get_leaderboards()

        if not leaderboards:
            await send_discord_message(interaction, "No leaderboards found.", ephemeral=True)
            return

        # Create embed
        embed = discord.Embed(title="Active Leaderboards", color=discord.Color.blue())
        padding = " " * 4
        header = f"{'Name':<20}{padding}{'Deadline':<10}{padding}{'GPU Types':<18}\n"
        divider = "-" * 56  # Fixed discord bot length
        rows = []

        # Add fields for each leaderboard
        for lb in leaderboards:
            name_lines = textwrap.wrap(lb["name"], 20)
            gpu_types_lines = textwrap.wrap(", ".join(lb["gpu_types"]), 18)

            # Text wrapping logic for long names / multiple GPU displays
            max_lines = max(len(name_lines), len(gpu_types_lines), 1)
            deadline_str = lb["deadline"].strftime("%Y-%m-%d")

            for i in range(max_lines):
                name_part = name_lines[i] if i < len(name_lines) else ""
                deadline_part = deadline_str if i == 0 else ""  # Deadline only on the first row
                gpu_types_part = gpu_types_lines[i] if i < len(gpu_types_lines) else ""

                rows.append(
                    f"{name_part:<20}{padding}{deadline_part:<10}{padding}{gpu_types_part:<18}"
                )

        # Add the formatted text to the embed as a code block
        embed.description = f"```\n{header}{divider}\n" + "\n".join(rows) + "\n```"

        await interaction.followup.send("", embed=embed)

    @discord.app_commands.describe(
        leaderboard_name="Name of the leaderboard",
        deadline="Competition deadline in the form: 'Y-m-d'",
        reference_code="Reference implementation of kernel. Also includes eval code.",
    )
    async def leaderboard_create(
        self,
        interaction: discord.Interaction,
        leaderboard_name: str,
        deadline: str,
        reference_code: discord.Attachment,
    ):
        # Try parsing with time first
        try:
            date_value = datetime.strptime(deadline, "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                date_value = datetime.strptime(deadline, "%Y-%m-%d")
            except ValueError as ve:
                logger.error(f"Value Error: {str(ve)}", exc_info=True)
                await send_discord_message(
                    interaction,
                    "Invalid date format. Please use YYYY-MM-DD or YYYY-MM-DD HH:MM",
                    ephemeral=True,
                )
                return

        # Ask the user to select GPUs
        view = GPUSelectionView([gpu.name for gpu in GitHubGPU], interaction.user)

        await send_discord_message(
            interaction,
            "Please select GPUs for this leaderboard.",
            view=view,
            ephemeral=True,
        )

        await view.wait()

        try:
            # Read the template file
            template_content = await reference_code.read()

            with self.bot.leaderboard_db as db:
                err = db.create_leaderboard(
                    {
                        "name": leaderboard_name,
                        "deadline": date_value,
                        "reference_code": template_content.decode("utf-8"),
                        "gpu_types": view.selected_gpus,
                    }
                )

                if err:
                    if "duplicate key" in err:
                        await send_discord_message(
                            interaction,
                            "Error: Tried to create a leaderboard "
                            f'"{leaderboard_name}" that already exists.',
                            ephemeral=True,
                        )
                    else:
                        # Handle any other errors
                        logger.error(f"Error in leaderboard creation: {err}")
                        await send_discord_message(
                            interaction,
                            "Error in leaderboard creation.",
                            ephemeral=True,
                        )
                    return

            await send_discord_message(
                interaction,
                f"Leaderboard '{leaderboard_name}'.\n"
                + f"Reference code: {reference_code}. Submission deadline: {date_value}",
                ephemeral=True,
            )

        except Exception as e:
            logger.error(f"Error in leaderboard creation: {e}")
            # Handle any other errors
            await send_discord_message(
                interaction,
                "Error in leaderboard creation.",
                ephemeral=True,
            )

    @discord.app_commands.describe(leaderboard_name="Name of the leaderboard")
    @app_commands.autocomplete(leaderboard_name=leaderboard_name_autocomplete)
    async def get_leaderboard_submissions(
        self,
        interaction: discord.Interaction,
        leaderboard_name: str,
    ):
        try:
            submissions = {}
            with self.bot.leaderboard_db as db:
                # TODO: query that gets leaderboard id given leaderboard name
                leaderboard_id = db.get_leaderboard(leaderboard_name)["id"]
                if not leaderboard_id:
                    await send_discord_message(
                        interaction,
                        f'Leaderboard "{leaderboard_name}" not found.',
                        ephemeral=True,
                    )
                    return

                gpus = db.get_leaderboard_gpu_types(leaderboard_name)
                for gpu in gpus:
                    submissions[gpu] = db.get_leaderboard_submissions(leaderboard_name, gpu)

            if not interaction.response.is_done():
                await interaction.response.defer()

            view = GPUSelectionView(gpus, interaction.user)

            await send_discord_message(
                interaction,
                f"Please select GPUs view for leaderboard: {leaderboard_name}.",
                view=view,
                ephemeral=True,
            )

            await view.wait()

            for gpu in view.selected_gpus:
                await display_lb_submissions(interaction, self.bot, leaderboard_name, gpu)

        except Exception as e:
            logger.error(str(e))
            if "'NoneType' object is not subscriptable" in str(e):
                await send_discord_message(
                    interaction,
                    f"The leaderboard '{leaderboard_name}' doesn't exist.",
                    ephemeral=True,
                )
            else:
                await send_discord_message(
                    interaction, "An unknown error occurred.", ephemeral=True
                )

    @discord.app_commands.describe(leaderboard_name="Name of the leaderboard")
    @discord.app_commands.autocomplete(leaderboard_name=leaderboard_name_autocomplete)
    async def delete_leaderboard(self, interaction: discord.Interaction, leaderboard_name: str):
        modal = DeleteConfirmationModal(leaderboard_name, self.bot.leaderboard_db)
        await interaction.response.send_modal(modal)
