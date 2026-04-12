# Homebrew formula for Engram
# Install via:
#   brew tap vascoclerigo/engram
#   brew install engram
#
# Before publishing, replace the url and sha256 with a real release tarball.
# Generate sha256 with: shasum -a 256 engram-0.1.0.tar.gz

class Engram < Formula
  include Language::Python::Virtualenv

  desc     "Agnostic learning layer for decision systems"
  homepage "https://github.com/vascoclerigo/engram"
  url      "https://github.com/vascoclerigo/engram/archive/refs/tags/v0.1.0.tar.gz"
  sha256   "REPLACE_WITH_REAL_SHA256_AFTER_TAGGING"
  license  "MIT"

  depends_on "python@3.12"

  resource "anthropic" do
    url    "https://files.pythonhosted.org/packages/source/a/anthropic/anthropic-0.49.0.tar.gz"
    sha256 "REPLACE_WITH_REAL_SHA256"
  end

  resource "pyyaml" do
    url    "https://files.pythonhosted.org/packages/source/P/PyYAML/PyYAML-6.0.2.tar.gz"
    sha256 "REPLACE_WITH_REAL_SHA256"
  end

  resource "click" do
    url    "https://files.pythonhosted.org/packages/source/c/click/click-8.1.7.tar.gz"
    sha256 "REPLACE_WITH_REAL_SHA256"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    system bin/"engram", "--version"
    # Scaffold a schema in a temp dir and verify the file is created
    Dir.mktmpdir do |dir|
      system bin/"engram", "init", "--path", dir
      assert_predicate Pathname(dir)/"engram.yaml", :exist?
    end
  end
end
