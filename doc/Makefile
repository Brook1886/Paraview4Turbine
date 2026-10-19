# The manual. `make` builds veclys-postprocessor.pdf; `make clean` removes the rest.
PDF  := veclys-postprocessor.pdf
TEX  := veclys-postprocessor.tex
FIGS := $(wildcard figures/*.png)

$(PDF): $(TEX) $(FIGS)
	latexmk -pdf -quiet -halt-on-error $(TEX)

clean:
	latexmk -c
	rm -f $(PDF)

.PHONY: clean
