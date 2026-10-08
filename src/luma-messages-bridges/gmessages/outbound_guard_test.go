// SPDX-License-Identifier: AGPL-3.0-or-later
package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Calls that make the phone deliver something to another person. Only
// outbound.go may name them.
var deliveryCalls = map[string]bool{"SendMessage": true, "SendReaction": true, "UploadMedia": true}

// Calls that ask the phone to act on a message on its own. Nothing may name
// them. GetFullSizeImage was the automatic "ask the phone for the full file"
// of 0.6 to 0.8; it is allowed back in exactly one place (fullsize.go's
// requestFullSize), reached only from askPhoneLocked, which is reached only
// from a person's Try Again in the command handler. See phoneAskCalls.
var forbiddenCalls = map[string]bool{
	"ResendMessage": true, "ForwardMessage": true, "SendTypingUpdate": true,
	"sendUserMessage": true, "sendMessageWithParams": true, "requestFull": true,
}

// Each full-size request call and the only file and function allowed to make it.
var phoneAskCalls = map[string][2]string{
	"GetFullSizeImage": {"fullsize.go", "requestFullSize"},
	"requestFullSize":  {"fullsize.go", "askPhoneLocked"},
	"askPhoneLocked":   {"fullsize.go", "askPhone|pressedBeforeLookupLocked"},
	"askPhone":         {"main.go", "handle"},
}

// TestOnlyOutboundCanDeliver is the structural guarantee: it reads every source
// file of the helper and fails if a delivery call appears outside outbound.go,
// if a forbidden call appears anywhere, if outbound's delivery methods are used
// anywhere but the helper's user-command handler, or if a userAction is made
// anywhere but from a command's arguments.
func TestOnlyOutboundCanDeliver(t *testing.T) {
	files, _ := filepath.Glob("*.go")
	fset := token.NewFileSet()
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") {
			continue
		}
		source, err := os.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		file, err := parser.ParseFile(fset, name, source, 0)
		if err != nil {
			t.Fatal(err)
		}
		function := ""
		// Selectors that are called (x.M(...)), as opposed to declared or referenced.
		parents := map[*ast.SelectorExpr]bool{}
		ast.Inspect(file, func(node ast.Node) bool {
			if call, ok := node.(*ast.CallExpr); ok {
				if sel, ok := call.Fun.(*ast.SelectorExpr); ok {
					parents[sel] = true
				}
			}
			return true
		})
		ast.Inspect(file, func(node ast.Node) bool {
			if decl, ok := node.(*ast.FuncDecl); ok {
				function = decl.Name.Name
			}
			switch n := node.(type) {
			case *ast.SelectorExpr:
				method := n.Sel.Name
				if allowed, ok := phoneAskCalls[method]; ok {
					if _, isCall := parents[n]; isCall && (name != allowed[0] || !strings.Contains("|"+allowed[1]+"|", "|"+function+"|")) {
						t.Errorf("%s: %s calls %s; only %s %s may", fset.Position(n.Pos()), function, method, allowed[0], allowed[1])
					}
				}
				if forbiddenCalls[method] {
					t.Errorf("%s: %s names %s, which is forbidden in this helper", fset.Position(n.Pos()), function, method)
				}
				if deliveryCalls[method] && name != "outbound.go" {
					t.Errorf("%s: %s calls %s outside outbound.go", fset.Position(n.Pos()), function, method)
				}
				if (method == "sendMessage" || method == "sendReaction" || method == "upload") && name != "outbound.go" &&
					!(name == "main.go" && (function == "send" || function == "react")) {
					t.Errorf("%s: %s reaches outbound.%s; only the user send and react commands may", fset.Position(n.Pos()), function, method)
				}
			case *ast.CallExpr:
				if ident, ok := n.Fun.(*ast.Ident); ok && ident.Name == "parseUserAction" && !(name == "main.go" && function == "handle") {
					t.Errorf("%s: %s makes a userAction; only the command handler may", fset.Position(n.Pos()), function)
				}
			case *ast.CompositeLit:
				if ident, ok := n.Type.(*ast.Ident); ok && ident.Name == "userAction" && !(name == "outbound.go" && function == "parseUserAction") {
					t.Errorf("%s: %s builds a userAction by hand", fset.Position(n.Pos()), function)
				}
			}
			return true
		})
	}
}

func TestTheMediaNetworkOffersNoDelivery(t *testing.T) {
	var network mediaNetwork = newFakeNet()
	if _, ok := network.(deliveryClient); ok {
		t.Fatal("the media queue's network can deliver")
	}
}
