function(options) {
  var opts = options || {};
  var results = {};

  // Math evaluation: safely evaluate arithmetic expressions
  if (opts.math) {
    try {
      // Sanitize: only allow digits, operators, parens, decimal points, whitespace
      var expr = String(opts.math).trim();
      if (!/^[\d\s+\-*\/().,%^]+$/.test(expr)) {
        results.math = 'Error: expression contains invalid characters';
      } else {
        // Replace ^ with ** for exponentiation
        expr = expr.replace(/\^/g, '**');
        // Use Function constructor for safe eval of math expressions
        var value = new Function('return (' + expr + ')')();
        results.math = String(value);
      }
    } catch (e) {
      results.math = 'Error: ' + e.message;
    }
  }

  // String reversal
  if (opts.reverse) {
    results.reverse = String(opts.reverse).split('').reverse().join('');
  }

  // Base64 decode
  if (opts.decode) {
    try {
      results.decode = atob(String(opts.decode));
    } catch (e) {
      results.decode = 'Error: invalid base64 - ' + e.message;
    }
  }

  // Base64 encode
  if (opts.encode) {
    try {
      results.encode = btoa(String(opts.encode));
    } catch (e) {
      results.encode = 'Error: ' + e.message;
    }
  }

  // Hex decode
  if (opts.hexDecode) {
    try {
      var hex = String(opts.hexDecode).replace(/\s/g, '');
      var str = '';
      for (var i = 0; i < hex.length; i += 2) {
        str += String.fromCharCode(parseInt(hex.substr(i, 2), 16));
      }
      results.hexDecode = str;
    } catch (e) {
      results.hexDecode = 'Error: ' + e.message;
    }
  }

  // ROT13
  if (opts.rot13) {
    results.rot13 = String(opts.rot13).replace(/[a-zA-Z]/g, function(c) {
      var base = c <= 'Z' ? 65 : 97;
      return String.fromCharCode(((c.charCodeAt(0) - base + 13) % 26) + base);
    });
  }

  // Caesar cipher with custom shift
  if (opts.caesar) {
    var shift = parseInt(opts.caesarShift) || 3;
    results.caesar = String(opts.caesar).replace(/[a-zA-Z]/g, function(c) {
      var base = c <= 'Z' ? 65 : 97;
      return String.fromCharCode(((c.charCodeAt(0) - base + shift + 26) % 26) + base);
    });
  }

  // String operations: uppercase, lowercase, length
  if (opts.upper) results.upper = String(opts.upper).toUpperCase();
  if (opts.lower) results.lower = String(opts.lower).toLowerCase();
  if (opts.length) results.length = String(opts.length).length;

  // Concatenate array of strings
  if (opts.concat && Array.isArray(opts.concat)) {
    results.concat = opts.concat.join('');
  }

  // Sort characters
  if (opts.sort) {
    results.sort = String(opts.sort).split('').sort().join('');
  }

  return JSON.stringify(results);
}
