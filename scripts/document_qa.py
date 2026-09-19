"""Render Word-exported PDFs for visual inspection with bundled PDFium."""
import argparse
import json
from pathlib import Path
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

def main():
    p=argparse.ArgumentParser(); p.add_argument('directory'); p.add_argument('output'); a=p.parse_args()
    out=Path(a.output); out.mkdir(parents=True,exist_ok=True)
    report={}
    for file in sorted(Path(a.directory).glob('*.pdf')):
        pdf=pdfium.PdfDocument(file)
        pages=[]; text_lengths=[]
        for i,page in enumerate(pdf):
            bitmap=page.render(scale=1.35).to_pil().convert('RGB')
            bitmap.save(out/f'{file.stem}-{i+1:02}.png')
            pages.append(bitmap)
            text_lengths.append(len(page.get_textpage().get_text_range()))
        for start in range(0,len(pages),3):
            subset=pages[start:start+3]
            board=Image.new('RGB',(sum(im.width for im in subset),max(im.height for im in subset)+35),'#dddddd')
            x=0
            for offset,im in enumerate(subset):
                board.paste(im,(x,35)); ImageDraw.Draw(board).text((x+12,8),f'{file.stem} / {start+offset+1}',fill='black'); x+=im.width
            board.save(out/f'{file.stem}-contact-{start//3+1:02}.png')
        report[file.name]={'pages':len(pages),'characters_per_page':text_lengths}
    (out/'pagination.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
if __name__=='__main__': main()
