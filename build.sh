# build.sh — build helpers; source with ". ./build.sh".

build() { # nproc counts only the cores we are allowed on
  cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo > /dev/null && cmake --build build -j "$(nproc)" > /dev/null
}

bench_bin() { # bench binary for HANDLER=cpptrader|null|synth
  case "${HANDLER:-cpptrader}" in
    cpptrader) echo build/bench ;;
    *) echo "build/bench-$HANDLER" ;;
  esac
}
