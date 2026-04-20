import logging
import os
from PIL import Image
import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Dictionary to store images for each user: {user_id: [image_path1, image_path2, ...]}
user_image_queues = {}

async def start(update: Update, context) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! Send me images, and I'll merge them into a single PDF for you. "
        "Send one or more images, then press the 'Done' button.",
    )

async def handle_media(update: Update, context) -> None:
    """Handle incoming media (photos or documents)."""
    user_id = update.effective_user.id
    file_id = None
    file_name = None
    mime_type = None

    if update.message.photo:
        photo = update.message.photo[-1]
        file_id = photo.file_id
        file_name = f"photo_{file_id}.jpg"
        mime_type = "image/jpeg"
        await update.message.reply_text("Received photo. Add more or press 'Done'.")
    elif update.message.document:
        document = update.message.document
        file_id = document.file_id
        file_name = document.file_name
        mime_type = document.mime_type
        await update.message.reply_text(f"Received file: {file_name}. Add more or press 'Done'.")
    else:
        await update.message.reply_text("I can only convert images (sent as photos or documents) to PDF. Please send an image file.")
        return

    if mime_type and mime_type.startswith('image/'):
        try:
            # Get file_path from Telegram File object
            file_object = await context.bot.get_file(file_id)
            telegram_file_path = file_object.file_path
            logger.info(f"Telegram file path from get_file: {telegram_file_path}")

            if not telegram_file_path:
                await update.message.reply_text("Could not get file path from Telegram. Cannot download.")
                return

            # Use the telegram_file_path directly as the download_url
            # It appears that get_file already returns the full download URL.
            download_url = telegram_file_path

            # Ensure the user's temporary directory exists
            user_temp_dir = os.path.join("/tmp", str(user_id))
            os.makedirs(user_temp_dir, exist_ok=True)

            download_path = os.path.join(user_temp_dir, file_name)

            # Use httpx to download the file
            async with httpx.AsyncClient() as client:
                response = await client.get(download_url)
                response.raise_for_status() # Raise an exception for HTTP errors

                with open(download_path, "wb") as f:
                    f.write(response.content)

            # Add downloaded image path to user's queue
            if user_id not in user_image_queues:
                user_image_queues[user_id] = []
            user_image_queues[user_id].append(download_path)

            # Send the Done button
            keyboard = [[InlineKeyboardButton("Done", callback_data='done')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("Images received. Send more or press Done.", reply_markup=reply_markup)

        except Exception as e:
            logger.error(f"Error processing image: {e}")
            await update.message.reply_text(f"Sorry, I couldn't process that image. Error: {e}")
    else:
        await update.message.reply_text(f"I can only convert images to PDF for now. Received a non-image file type: {mime_type}")

async def button(update: Update, context) -> None:
    """Parses the CallbackQuery and updates the message text."""
    query = update.callback_query
    user_id = query.from_user.id

    await query.answer() # Acknowledge the callback query

    if query.data == 'done':
        if user_id not in user_image_queues or not user_image_queues[user_id]:
            await query.edit_message_text(text="No images received to convert. Please send images first.")
            return

        await query.edit_message_text(text="Merging images into PDF...")

        image_paths = user_image_queues[user_id]
        pdf_output_path = os.path.join("/tmp", f"merged_pdf_{user_id}.pdf")

        try:
            images = []
            for path in image_paths:
                img = Image.open(path).convert("RGB")
                images.append(img)

            if not images:
                await query.edit_message_text(text="No images found for conversion.")
                return

            images[0].save(
                pdf_output_path, "PDF", save_all=True, append_images=images[1:]
            )

            await context.bot.send_document(
                chat_id=user_id,
                document=open(pdf_output_path, 'rb'),
                filename=os.path.basename(pdf_output_path),
                caption="Here is your merged PDF!"
            )
            await query.edit_message_text(text="Merged PDF sent successfully!")

        except Exception as e:
            logger.error(f"Error merging images to PDF: {e}")
            await query.edit_message_text(text=f"Sorry, I couldn't merge the images into a PDF. Error: {e}")
        finally:
            # Clean up temporary files
            for path in image_paths:
                if os.path.exists(path):
                    os.remove(path)
            if os.path.exists(pdf_output_path):
                os.remove(pdf_output_path)
            if user_id in user_image_queues:
                del user_image_queues[user_id] # Clear user's queue

def main() -> None:
    """Start the bot."""
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_media))
    application.add_handler(CallbackQueryHandler(button))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
