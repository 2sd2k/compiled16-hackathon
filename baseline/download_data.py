"""Download only named public study archives; raw data stay outside the repository."""
import argparse
from pathlib import Path
import urllib.request

SOURCES = {
    'hku': ('https://ndownloader.figshare.com/files/38149263', 'HKU956.zip'),
    'music-mouv': ('https://homepages.loria.fr/gbonnin/music-mouv/music-mouv.zip', 'music-mouv.zip'),
}

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('dataset', choices=SOURCES)
    p.add_argument('--directory', type=Path, required=True, help='Local raw-data directory; do not commit it')
    args = p.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    url, filename = SOURCES[args.dataset]
    destination = args.directory/filename
    partial = destination.with_suffix('.part')
    urllib.request.urlretrieve(url, partial)
    partial.replace(destination)
    print(destination)
