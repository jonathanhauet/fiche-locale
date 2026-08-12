"""
Ajout de coordonnees GPS dans les metadonnees EXIF d'une photo (geotagging
optionnel). Reencode toujours en JPEG : l'EXIF standard ne s'applique pas de
maniere fiable aux autres formats (PNG, WebP, etc.).
"""

import io

import piexif
from PIL import Image


def _degres_vers_dms(valeur_absolue: float):
    degres = int(valeur_absolue)
    minutes_flottant = (valeur_absolue - degres) * 60
    minutes = int(minutes_flottant)
    secondes = round((minutes_flottant - minutes) * 60 * 100)
    return ((degres, 1), (minutes, 1), (secondes, 100))


def ajouter_geotag(octets_image: bytes, latitude: float, longitude: float) -> bytes:
    """Renvoie l'image reencodee en JPEG avec latitude/longitude inscrites dans l'EXIF GPS."""
    image = Image.open(io.BytesIO(octets_image)).convert("RGB")

    exif_gps = {
        piexif.GPSIFD.GPSLatitudeRef: "N" if latitude >= 0 else "S",
        piexif.GPSIFD.GPSLatitude: _degres_vers_dms(abs(latitude)),
        piexif.GPSIFD.GPSLongitudeRef: "E" if longitude >= 0 else "W",
        piexif.GPSIFD.GPSLongitude: _degres_vers_dms(abs(longitude)),
    }
    octets_exif = piexif.dump({"GPS": exif_gps})

    tampon = io.BytesIO()
    image.save(tampon, format="JPEG", quality=92, exif=octets_exif)
    return tampon.getvalue()
