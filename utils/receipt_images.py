from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIZE = (1600, 1600)


def optimize_receipt_image(image_data: bytes) -> bytes:
    if not image_data or len(image_data) > MAX_UPLOAD_BYTES:
        raise ValueError("Choose a receipt image smaller than 10 MB.")
    try:
        with Image.open(BytesIO(image_data)) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail(MAX_IMAGE_SIZE)
            if image.mode != "RGB":
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", optimize=True, quality=85)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("That file is not a supported receipt image.") from exc
