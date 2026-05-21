" Use Ctrl+\ then 'g' to find Definition
nmap <C-\>g :cs find g <C-R>=expand("<cword>")<CR><CR>

" Use Ctrl+\ then 'c' to find Callers
nmap <C-\>c :cs find c <C-R>=expand("<cword>")<CR><CR>

" Use Ctrl+\ then 's' to find C Symbol
nmap <C-\>s :cs find s <C-R>=expand("<cword>")<CR><CR>

" Use Ctrl+\ then 't' to find Text matches
nmap <C-\>t :cs find t <C-R>=expand("<cword>")<CR><CR>
