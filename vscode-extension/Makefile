.PHONY: build package clean install

build:
	npm install
	npm run compile

package: build
	npx vsce package --no-dependencies

install: package
	code --install-extension codegraph-mcp-*.vsix

clean:
	rm -rf out node_modules *.vsix
