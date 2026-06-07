$latex = 'platex -synctex=1 -halt-on-error -file-line-error %O %S';
$bibtex = 'pbibtex %O %B';
$dvipdf = 'dvipdfmx %O -o %D %S';
$makeindex = 'mendex %O -o %D %S';
$pdf_mode = 3;
$max_repeat = 5;

@generated_exts = (@generated_exts, qw(dvi synctex.gz));
